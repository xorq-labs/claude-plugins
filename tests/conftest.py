"""Shared fixtures for the xorq CLI contract tests.

The contract tests run the *real* ``xorq`` binary inside a hermetic, temporary
``HOME`` so that ``~/.config/xorq/catalog-default`` and
``~/.local/share/xorq/catalogs/`` are isolated per test and never touch the
developer's (or CI's) real catalogs.

The binary under test is, in order of preference:
  1. ``$XORQ_BIN`` if set,
  2. an ``xorq`` next to the running Python interpreter (so ``uv run pytest``
     and ``venv/bin/python -m pytest`` test the *same* environment's xorq),
  3. whatever ``xorq`` is on ``PATH``.
If none is found the tests skip rather than fail.
"""

import csv
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"


def _find_xorq_bin():
    if env := os.environ.get("XORQ_BIN"):
        return env
    sibling = Path(sys.executable).parent / "xorq"
    if sibling.exists():
        return str(sibling)
    return shutil.which("xorq")


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str

    @property
    def output(self):
        # xorq prints results to stdout and click errors to stderr; assertions
        # generally don't care which stream, so expose the concatenation.
        return self.stdout + self.stderr


@dataclass
class XorqCli:
    bin: str
    home: Path
    base_env: dict

    def run(self, *args, env=None):
        full_env = dict(self.base_env)
        if env:
            full_env.update(env)
        proc = subprocess.run(
            [self.bin, *args],
            capture_output=True,
            text=True,
            env=full_env,
        )
        return Result(proc.returncode, proc.stdout or "", proc.stderr or "")

    @property
    def catalog_default_file(self) -> Path:
        """The persisted default-name file (CLAUDE.md: ~/.config/xorq/catalog-default)."""
        return self.home / ".config" / "xorq" / "catalog-default"

    def named_catalog_dir(self, name: str) -> Path:
        """Location of a named catalog (CLAUDE.md: ~/.local/share/xorq/catalogs/<name>)."""
        return self.home / ".local" / "share" / "xorq" / "catalogs" / name


@pytest.fixture(scope="session")
def xorq_bin():
    binp = _find_xorq_bin()
    if not binp:
        pytest.skip("xorq binary not found (set XORQ_BIN or install xorq on PATH)")
    return binp


@pytest.fixture
def xorq(xorq_bin, tmp_path):
    """A hermetic xorq CLI runner with an isolated temporary HOME."""
    home = tmp_path / "home"
    home.mkdir()
    # git identity so `catalog init`'s initial commit succeeds under the temp HOME
    (home / ".gitconfig").write_text(
        "[user]\n\tname = xorq contract tests\n\temail = tests@xorq.dev\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "XORQ_DEFAULT_CATALOG"}
    env["HOME"] = str(home)
    env["XDG_CONFIG_HOME"] = str(home / ".config")
    env["XDG_DATA_HOME"] = str(home / ".local" / "share")
    return XorqCli(bin=xorq_bin, home=home, base_env=env)


# ---------------------------------------------------------------------------
# Shared data-source builders
#
# Used both by the deterministic ingest contract tests (session-scoped DBs) and
# by the LLM e2e tests (which seed these into a temp project the skill ingests
# from). The schemas mirror tests/data/customers.csv — the single source of
# truth — so no binary DB fixtures are committed.
# ---------------------------------------------------------------------------


def need_data(*names):
    """Skip the calling test if any required tests/data file is missing."""
    missing = [n for n in names if not (DATA / n).exists()]
    if missing:
        pytest.skip(f"missing tests/data: {', '.join(missing)}")


def build_sqlite_db(dest: Path) -> Path:
    """Create a sqlite db at ``dest`` with a ``customers`` table from customers.csv."""
    con = sqlite3.connect(dest)
    con.execute(
        "CREATE TABLE customers (customer_id INTEGER, name TEXT, age INTEGER, "
        "state TEXT, signup_date TEXT, tier TEXT)"
    )
    with open(DATA / "customers.csv") as f:
        con.executemany("INSERT INTO customers VALUES (?,?,?,?,?,?)", list(csv.reader(f))[1:])
    con.commit()
    con.close()
    return dest


def build_duckdb_db(dest: Path) -> Path:
    """Create a duckdb db at ``dest`` with a ``customers`` table from customers.csv."""
    import duckdb

    con = duckdb.connect(str(dest))
    con.execute(
        f"CREATE TABLE customers AS SELECT * FROM read_csv_auto({str(DATA / 'customers.csv')!r})"
    )
    con.close()
    return dest


# ---------------------------------------------------------------------------
# LLM e2e (claude -p) — drive the headless CLI with the xorq plugin loaded
# ---------------------------------------------------------------------------

PLUGIN_DIR = REPO / "xorq"
CLAUDE_MD = PLUGIN_DIR / "CLAUDE.md"

# The only steer we inject: headless `-p` has no human, but CLAUDE.md's Catalog
# Resolution (Step 0) tells the model to *ask* how to resolve the catalog when none
# exists. This removes that one blocker — it does NOT tell the model how to ingest.
STEER = (
    "Non-interactive session: when a skill would ask the user a question, pick the "
    "recommended default and proceed without asking. For catalog resolution, create a "
    "new repo-local catalog in the current working directory."
)

POSTGRES_ENV = {
    "POSTGRES_HOST": os.environ.get("POSTGRES_HOST", "localhost"),
    "POSTGRES_PORT": os.environ.get("POSTGRES_PORT", "5432"),
    "POSTGRES_DB": os.environ.get("POSTGRES_DB", "xorq"),
    "POSTGRES_USER": os.environ.get("POSTGRES_USER", "xorq"),
    "POSTGRES_PASSWORD": os.environ.get("POSTGRES_PASSWORD", "xorq"),
}


def postgres_reachable() -> bool:
    """True if a TCP connection to the configured Postgres opens within 1s."""
    try:
        with socket.create_connection(
            (POSTGRES_ENV["POSTGRES_HOST"], int(POSTGRES_ENV["POSTGRES_PORT"])), timeout=1
        ):
            return True
    except OSError:
        return False


@dataclass
class ClaudeRun:
    """Outcome of one headless claude session: the parsed result + where any catalog
    it created might live (repo-local in the project, or a named catalog under the
    isolated XDG data home)."""

    result: dict
    project: Path
    xdg_data: Path

    @property
    def catalog_search_dirs(self) -> list:
        return [self.project, self.xdg_data / "xorq" / "catalogs"]

    @property
    def is_error(self) -> bool:
        return bool(self.result.get("is_error"))

    @property
    def said(self) -> str:
        prefix = "[session ended on is_error] " if self.is_error else ""
        return prefix + str(self.result.get("result", ""))[:1500]

    @property
    def answer(self) -> str:
        """The model's full final text (untruncated) — what a read-only skill is judged on."""
        return str(self.result.get("result", ""))


@pytest.fixture(scope="session")
def claude_bin():
    binp = shutil.which("claude")
    if not binp:
        pytest.skip("claude CLI not found (npm install -g @anthropic-ai/claude-code)")
    return binp


@pytest.fixture(scope="session")
def claude_auth():
    """Skip the LLM suite when headless claude has no way to authenticate."""
    has_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not has_key and not (Path.home() / ".claude").exists():
        pytest.skip(
            "no claude auth (set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, or log in — ~/.claude missing)"
        )


@pytest.fixture
def claude_project(tmp_path):
    """A clean, wheel-buildable tmp project dir the ingest skill treats as repo-local.

    Bare on purpose: only pyproject.toml + lockfile (so ``xorq catalog add`` can build a
    wheel) and NO catalog.yaml (so resolution creates a fresh repo-local catalog). Each
    test seeds exactly the source(s) its prompt targets via ``seed_files`` / ``seed_sqlite``
    / ``seed_duckdb`` — so e.g. "load my raw data" can't sweep up an unrelated ``app.db``
    that only the SQLite prompt should see.
    """
    proj = tmp_path / "project"
    proj.mkdir()
    for name in ("pyproject.toml", "uv.lock", ".python-version"):
        src = REPO / name
        if src.exists():
            shutil.copy(src, proj / name)
    return proj


def seed_files(proj: Path, *names) -> Path:
    """Symlink the named tests/data files into ``proj/data`` (builds embed absolute paths)."""
    need_data(*names)
    data = proj / "data"
    data.mkdir(exist_ok=True)
    for n in names:
        link = data / n
        if not link.exists():
            link.symlink_to((DATA / n).resolve())
    return data


def seed_builder_module(proj: Path) -> Path:
    """Copy the trivial custom-builder fixture (slice_builder.py) into the project root.

    Lets the agent ``import slice_builder`` for the custom-builder e2e — importing it registers
    the ``"text_slice"`` TagHandler, so a tagged expr round-trips (recover via ``.ls.builder``,
    switch the option, build a new expr). It is NOT an entry point, so recovery only works
    in-process — which is exactly what the skill's custom-handler path documents.
    """
    need_data("slice_builder.py")
    dst = proj / "slice_builder.py"
    shutil.copy(DATA / "slice_builder.py", dst)
    return dst


def seed_sqlite(proj: Path) -> Path:
    """Create ``proj/app.db`` (sqlite) with a customers table from customers.csv."""
    need_data("customers.csv")
    return build_sqlite_db(proj / "app.db")


def seed_duckdb(proj: Path) -> Path:
    """Create ``proj/warehouse.duckdb`` with a customers table from customers.csv."""
    need_data("customers.csv")
    return build_duckdb_db(proj / "warehouse.duckdb")


# ---------------------------------------------------------------------------
# Deterministic catalog seeding for the composer e2e
#
# Stand up a repo-local catalog with the source files ALREADY added (alias == key),
# so the composer LLM run resolves it and composes on top — we don't retest ingest.
# CSV / parquet only (deferred_read_*); no DB backends are involved here.
# ---------------------------------------------------------------------------

SOURCES = {  # alias -> tests/data file
    "customers": "customers.csv",
    "products": "products.csv",
    "transactions": "transactions.csv",
    "events_dev": "events_dev.parquet",
    "events_prod": "events_prod.parquet",
}


def _seed(xorq_bin, proj: Path, *args):
    """Run xorq in ``proj`` (wheel-buildable) for deterministic seeding; fail loudly.

    HOME stays real (warm uv cache); git identity is forced via env so ``catalog init``'s
    commit succeeds even where HOME has no .gitconfig (CI); xorq state (XDG + parquet cache)
    is isolated so the seed is safe under ``pytest -n``.
    """
    env = {k: v for k, v in os.environ.items() if k != "XORQ_DEFAULT_CATALOG"}
    env.setdefault("GIT_AUTHOR_NAME", "xorq seed")
    env.setdefault("GIT_AUTHOR_EMAIL", "seed@xorq.dev")
    env.setdefault("GIT_COMMITTER_NAME", "xorq seed")
    env.setdefault("GIT_COMMITTER_EMAIL", "seed@xorq.dev")
    home = proj / ".seed-home"
    env["XDG_DATA_HOME"] = str(home / "data")
    env["XDG_CONFIG_HOME"] = str(home / "config")
    env["XORQ_CACHE_DIR"] = str(home / "cache")
    r = subprocess.run(
        [xorq_bin, *map(str, args)], cwd=str(proj), env=env,
        capture_output=True, text=True, timeout=300,
    )
    assert r.returncode == 0, (
        f"seed step failed: xorq {' '.join(map(str, args))}\n"
        f"STDOUT:\n{r.stdout}\nSTDERR:\n{r.stderr}"
    )
    return r


def seed_catalog_sources(xorq_bin, proj: Path, names) -> Path:
    """Create a repo-local catalog in ``proj`` with the named sources added (alias == name).

    Returns the catalog path. The catalog resolution procedure finds it as the single
    repo-local candidate, so the composer LLM run composes on top of these entries.
    """
    data = seed_files(proj, *(SOURCES[n] for n in names))
    cat = proj / f"{proj.name}-catalog"
    _seed(xorq_bin, proj, "catalog", "-p", str(cat), "init")
    for alias in names:
        f = (data / SOURCES[alias]).resolve()
        reader = "deferred_read_parquet" if f.suffix == ".parquet" else "deferred_read_csv"
        script = proj / f"_seed_{alias}.py"
        script.write_text(f"import xorq.api as xo\nexpr = xo.{reader}({str(f)!r})\n")
        bp = proj / f"_bp_{alias}.txt"
        _seed(xorq_bin, proj, "build", script.name,
              "--builds-dir", "builds_source", "--emit-build-path-to", str(bp))
        _seed(xorq_bin, proj, "catalog", "-p", str(cat), "add", bp.read_text().strip(), "-a", alias)
    return cat


@pytest.fixture
def run_claude(claude_bin, claude_auth, claude_project, tmp_path):
    """Returns ``run(prompt, *, env_extra=None, timeout=300) -> ClaudeRun``.

    Each call runs ``claude -p`` headless with the xorq plugin loaded and xorq/CLAUDE.md
    injected, in the temp project (cwd). Per-test isolation (so the suite is safe under
    ``pytest -n``): the catalog store + profiles via a redirected XDG home, and the parquet
    cache via a unique ``XORQ_CACHE_DIR`` under /tmp. HOME is left real so claude's own auth
    keeps working. The /tmp cache dir is removed on teardown.
    """
    append = CLAUDE_MD.read_text() + "\n\n" + STEER
    # Optionally pin the model (e.g. XORQ_CLAUDE_PLUGIN_TEST_MODEL=sonnet to run the
    # Opus-slow llm suite faster/cheaper). Unset -> claude's session default.
    model = os.environ.get("XORQ_CLAUDE_PLUGIN_TEST_MODEL")
    xdg_data = tmp_path / "xdg-data"
    xdg_config = tmp_path / "xdg-config"
    xdg_data.mkdir()
    xdg_config.mkdir()
    cache_dir = tempfile.mkdtemp(prefix="xorq-test-cache-", dir="/tmp")

    def _run(prompt, *, env_extra=None, timeout=420) -> ClaudeRun:
        env = os.environ.copy()
        env.pop("XORQ_DEFAULT_CATALOG", None)
        env["XDG_DATA_HOME"] = str(xdg_data)
        env["XDG_CONFIG_HOME"] = str(xdg_config)
        env["XORQ_CACHE_DIR"] = cache_dir
        if env_extra:
            env.update(env_extra)
        argv = [
            claude_bin, "-p", str(prompt),
            "--plugin-dir", str(PLUGIN_DIR),
            "--output-format", "json",
            # Grant the tools the ingest skill needs (Skill to load it, Bash to drive xorq,
            # Write for the ingest script) rather than bypassing permissions wholesale.
            "--allowedTools", "Bash Edit Write Read Glob Grep Skill",
            "--append-system-prompt", append,
        ]
        if model:
            argv += ["--model", model]
        try:
            proc = subprocess.run(
                argv, cwd=str(claude_project), env=env,
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            pytest.fail(f"claude -p timed out after {timeout}s\n{e.stdout}\n{e.stderr}")
        # NB: we deliberately do NOT assert proc.returncode == 0. A session can end on a
        # transient `is_error` (e.g. "API Error: socket connection closed") *after* the
        # skill already produced the catalog. The artifact is the source of truth, so let
        # the outcome assertions judge; the error text rides along in ClaudeRun.said for
        # legible failures when no catalog was in fact created.
        try:
            result = json.loads(proc.stdout)
        except json.JSONDecodeError:
            pytest.fail(
                "claude did not return JSON "
                f"(exit {proc.returncode}):\n{proc.stdout[-2000:]}\n{proc.stderr[-2000:]}"
            )
        return ClaudeRun(result=result, project=claude_project, xdg_data=xdg_data)

    yield _run
    shutil.rmtree(cache_dir, ignore_errors=True)


# --- outcome assertions: verify the deterministic artifacts the skill produced ---

_CAT_EXCLUDE = (".venv", "__pycache__", ".git", "entries", "aliases")


def _catalog_dirs(root: Path) -> list:
    out = []
    for p in root.rglob("catalog.yaml"):
        if any(part in _CAT_EXCLUDE or part.startswith("builds") for part in p.parts):
            continue
        out.append(p.parent)
    return out


def _xq(xorq_bin, *args, timeout=180):
    return subprocess.run([xorq_bin, *args], capture_output=True, text=True, timeout=timeout)


def catalog_kinds(xorq_bin, cat: Path) -> str:
    """stdout of ``xorq catalog -p <cat> list --kind`` (rows look like ``<hash>\\tsource``)."""
    return _xq(xorq_bin, "catalog", "-p", str(cat), "list", "--kind").stdout


def source_hashes(xorq_bin, cat: Path) -> list:
    """Content hashes of every ``source`` entry in a catalog (``list`` shows hashes)."""
    out = []
    for line in catalog_kinds(xorq_bin, cat).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "source":
            out.append(parts[0])
    return out


def source_entries(xorq_bin, run: ClaudeRun) -> list:
    """Every (catalog, hash) source entry the model created, across all places a
    catalog might land (repo-local in the project, or named under the isolated XDG home)."""
    cats = sorted({d for root in run.catalog_search_dirs if root.exists() for d in _catalog_dirs(root)})
    return [(cat, h) for cat in cats for h in source_hashes(xorq_bin, cat)]


def entry_schema(xorq_bin, cat: Path, ident: str) -> dict:
    """The stored ``schema_out`` ({name: type}) of a catalog entry — metadata, no execution."""
    return json.loads(_xq(xorq_bin, "catalog", "-p", str(cat), "schema", ident, "--json").stdout)["schema_out"]


def catalog_run_rows(xorq_bin, cat: Path, ident: str, limit=3) -> list:
    """Run an entry by hash/alias in the current venv (offline) and return JSON rows."""
    r = _xq(
        xorq_bin, "catalog", "-p", str(cat), "run", ident,
        "--use-this-venv", "-o", "-", "-f", "json", "--limit", str(limit),
    )
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip().startswith("{")]


def file_schema(path: Path) -> dict:
    """xorq's ``schema_out`` for a deferred read of a data file: {name: type_str}.

    This is exactly the schema an ingested source entry for that file must have,
    so the LLM tests derive their expectations from the fixtures (no drift) and compare.
    """
    import xorq.api as xo

    path = Path(path)
    read = xo.deferred_read_parquet if path.suffix == ".parquet" else xo.deferred_read_csv
    return {n: str(t) for n, t in read(str(path.resolve())).schema().items()}


def _canon(schema: dict, types: bool):
    return tuple(sorted(schema.items())) if types else tuple(sorted(schema))


def assert_sources(xorq_bin, run: ClaudeRun, expected_schemas: list, *, types=True, run_one=True):
    """Assert the model produced exactly one source entry per expected schema.

    ``expected_schemas`` is a list of {name: type} dicts (one per file the prompt implies);
    the match is a MULTISET over ``schema_out`` so schema-identical files (events_dev /
    events_prod) require two distinct entries, not one. ``types=False`` compares column
    names only (for DB-backed sources whose types vary by backend). ``run_one`` also
    executes one entry to prove the pipeline runs (skip where ``catalog run`` can't, e.g.
    materialized DuckDB in 0.3.28).
    """
    from collections import Counter

    entries = source_entries(xorq_bin, run)
    assert entries, f"no source entry created\nclaude said: {run.said}"
    got = [entry_schema(xorq_bin, cat, h) for cat, h in entries]
    want_c = Counter(_canon(s, types) for s in expected_schemas)
    got_c = Counter(_canon(s, types) for s in got)
    assert got_c == want_c, (
        "ingested sources don't match the expected schemas "
        f"(types={types}).\n  expected: {sorted(want_c.elements())}\n"
        f"  got:      {sorted(got_c.elements())}\n  claude said: {run.said}"
    )
    if run_one:
        cat, h = entries[0]
        assert catalog_run_rows(xorq_bin, cat, h), \
            f"source {h} produced no rows\nclaude said: {run.said}"


# --- composer outcome assertions: derived (composed/expr) entries + flexible value checks ---


def derived_entries(xorq_bin, run: ClaudeRun) -> list:
    """(catalog, hash) for every ``composed`` or ``expr`` entry the model created.

    Either kind counts: single-source shaping lands as ``composed``; a multi-source join
    (compose is single-input) lands as ``expr``. Source entries (the seeded inputs) are
    excluded — we only want the derived results.
    """
    out = []
    cats = sorted({d for root in run.catalog_search_dirs if root.exists() for d in _catalog_dirs(root)})
    for cat in cats:
        for line in catalog_kinds(xorq_bin, cat).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("composed", "expr"):
                out.append((cat, parts[0]))
    return out


def assert_grouped(rows: list, expected: dict, *, tol=0.02):
    """Assert a grouped aggregation, tolerant of model-chosen column names.

    Find the key column whose value-set covers ``expected``'s keys, then assert some
    numeric measure column matches ``expected`` per group within relative tolerance — so
    the model is free to name columns anything.
    """
    keys = set(map(str, expected))
    keycols = [c for c in rows[0] if keys <= {str(r[c]) for r in rows}]
    assert keycols, f"no column carries group keys {sorted(keys)}; cols={list(rows[0])}\nrows={rows[:6]}"
    for kc in keycols:
        by = {str(r[kc]): r for r in rows}
        measures = [
            c for c in rows[0]
            if c != kc and all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) for r in rows)
        ]
        for mc in measures:
            if all(abs(by[str(k)][mc] - v) <= abs(v) * tol + tol for k, v in expected.items()):
                return
    raise AssertionError(f"no measure column matches {expected}\ncols={list(rows[0])}\nrows={rows}")


def assert_argmax(rows: list, expected_top):
    """Assert the label with the largest numeric measure equals ``expected_top``.

    Robust to whether the model returned a sorted full table or just the top row, and to
    which measure it ranked by.
    """
    measures = [c for c in rows[0] if all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) for r in rows)]
    labels = [c for c in rows[0] if c not in measures]
    assert measures and labels, f"need a label + a measure column; cols={list(rows[0])}\nrows={rows}"
    cands = {str(max(rows, key=lambda r: r[m])[l]) for l in labels for m in measures}
    assert str(expected_top) in cands, f"expected top {expected_top!r}, argmax candidates {cands}\nrows={rows}"


def assert_derived(xorq_bin, run: ClaudeRun, check, *, limit=60):
    """Find a derived entry whose run satisfies ``check(rows)``; fail with claude's words."""
    ent = derived_entries(xorq_bin, run)
    assert ent, f"no composed/expr entry created\nclaude said: {run.said}"
    last = None
    for cat, h in ent:
        rows = catalog_run_rows(xorq_bin, cat, h, limit=limit)
        if not rows:
            last = "entry produced no rows"
            continue
        try:
            check(rows)
            return
        except AssertionError as e:
            last = e
    raise AssertionError(f"no derived entry matched expected\n  last: {last}\n  claude said: {run.said}")


# --- builder outcome assertions: round-trip ExprBuilders (expr_builder entries) ---
#
# Mirrors derived_entries/assert_derived but for the builder skill. `entries_by_kind` generalizes
# the finder to any kind set — the custom in-process builder's tag is decorative at `add` time
# (no entry point), so its result can land as `expr` rather than `expr_builder`. `catalog_run_code_rows`
# runs an entry through the skill's `-c 'source.ls.builder.<method>(...)'` round-trip path.


def entries_by_kind(xorq_bin, run: ClaudeRun, kinds) -> list:
    """(catalog, hash) for every entry whose ``list --kind`` kind is in ``kinds``."""
    kinds = set(kinds)
    out = []
    cats = sorted({d for root in run.catalog_search_dirs if root.exists() for d in _catalog_dirs(root)})
    for cat in cats:
        for line in catalog_kinds(xorq_bin, cat).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in kinds:
                out.append((cat, parts[0]))
    return out


def builder_entries(xorq_bin, run: ClaudeRun) -> list:
    """(catalog, hash) for every ``expr_builder`` entry the model created."""
    return entries_by_kind(xorq_bin, run, ("expr_builder",))


def assert_entry_runs(xorq_bin, run: ClaudeRun, check, *, kinds, limit=200):
    """Find an entry of one of ``kinds`` whose run satisfies ``check(rows)``; fail with claude's words."""
    ents = entries_by_kind(xorq_bin, run, kinds)
    assert ents, f"no entry of kinds {tuple(kinds)} created\nclaude said: {run.said}"
    last = None
    for cat, h in ents:
        rows = catalog_run_rows(xorq_bin, cat, h, limit=limit)
        if not rows:
            last = "entry produced no rows"
            continue
        try:
            check(rows)
            return
        except AssertionError as e:
            last = e
    raise AssertionError(
        f"no entry of kinds {tuple(kinds)} matched\n  last: {last}\n  claude said: {run.said}"
    )


# --- catalog-explore outcome assertions: a read-only skill is judged on its ANSWER ---
#
# catalog-explore creates no artifact, so the contract is twofold: the model's reported
# answer must name the right entries/columns (it could only know them by inspecting the
# catalog), AND the seeded catalog must be unchanged (the skill added/removed nothing).


def assert_answer_mentions(run: ClaudeRun, *needles, min_hits=None):
    """Assert the model's answer names each needle (case-insensitive).

    ``min_hits`` relaxes "all" to "at least N" where some summarization is acceptable
    (e.g. a long column list); default requires every needle.
    """
    said = run.answer.lower()
    hits = [n for n in needles if str(n).lower() in said]
    need = len(needles) if min_hits is None else min_hits
    assert len(hits) >= need, (
        f"answer named {len(hits)}/{len(needles)} of {list(needles)} (need {need})\n"
        f"claude said: {run.said}"
    )


def catalog_snapshot(xorq_bin, cat: Path):
    """(entries, aliases) of a catalog — to prove a read-only skill mutated nothing."""
    kinds = tuple(sorted(catalog_kinds(xorq_bin, cat).splitlines()))
    aliases = tuple(sorted(_xq(xorq_bin, "catalog", "-p", str(cat), "list-aliases").stdout.splitlines()))
    return (kinds, aliases)


def assert_read_only(xorq_bin, cat: Path, before):
    """Assert the catalog's entry/alias set is identical to ``before`` (explore must not mutate)."""
    after = catalog_snapshot(xorq_bin, cat)
    assert after == before, (
        f"catalog changed — explore must be read-only\n  before: {before}\n  after:  {after}"
    )
