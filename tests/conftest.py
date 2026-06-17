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

from __future__ import annotations

import csv
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import zipfile
from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path

import attrs
import duckdb
import pytest
import xorq.api as xo

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"


def _find_xorq_bin() -> str | None:
    if env := os.environ.get("XORQ_BIN"):
        return env
    sibling = Path(sys.executable).parent / "xorq"
    if sibling.exists():
        return str(sibling)
    return shutil.which("xorq")


@attrs.define
class Result:
    code: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        # xorq prints results to stdout and click errors to stderr; assertions
        # generally don't care which stream, so expose the concatenation.
        return self.stdout + self.stderr


@attrs.define
class XorqCli:
    bin: str
    home: Path
    base_env: dict

    def run(self, *args: str, env: dict | None = None) -> Result:
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
        """The persisted default-name file (reference.md: ~/.config/xorq/catalog-default)."""
        return self.home / ".config" / "xorq" / "catalog-default"

    def named_catalog_dir(self, name: str) -> Path:
        """Location of a named catalog (reference.md: ~/.local/share/xorq/catalogs/<name>)."""
        return self.home / ".local" / "share" / "xorq" / "catalogs" / name


@pytest.fixture(scope="session")
def xorq_bin() -> str:
    binp = _find_xorq_bin()
    if not binp:
        pytest.skip("xorq binary not found (set XORQ_BIN or install xorq on PATH)")
    return binp


@pytest.fixture
def xorq(xorq_bin: str, tmp_path: Path) -> XorqCli:
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


def need_data(*names: str) -> None:
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

# The only steer we inject: headless `-p` has no human, but a skill's catalog
# resolution tells the model to *ask* how to resolve the catalog when none exists.
# This removes that one blocker — it does NOT tell the model how to ingest. The plugin
# ships no CLAUDE.md; its SessionStart hook injects the shared essentials (skills/_shared/essentials.md).
#
# We do NOT steer the catalog *location*: the model should resolve it the way the skill says
# (non-interactively that means a NAMED catalog), so the suite tests the realistic flow. Isolation
# is handled by the run_claude fixture (per-test HOME when auth is token-based; reap-on-teardown
# otherwise), not by forcing repo-local.
STEER = (
    "Non-interactive session: when a skill would ask the user a question, pick the "
    "recommended default and proceed without asking."
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


def _stream_tail(path: Path, n: int) -> str:
    """Last ``n`` lines of a stream-json log, for legible timeout / no-result failures."""
    if not path.exists():
        return "(no stream log)"
    lines = path.read_text(errors="replace").splitlines()
    return "\n".join(lines[-n:]) if lines else "(empty stream log)"


def _stream_result(path: Path) -> dict | None:
    """The last ``{"type": "result", ...}`` event in a stream-json log, else ``None``.

    Skips undecodable lines (a killed session can leave a half-written final line).
    """
    if not path.exists():
        return None
    result = None
    for line in path.read_text(errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict) and ev.get("type") == "result":
            result = ev
    return result


@attrs.define
class ClaudeRun:
    """Outcome of one headless claude session: the parsed result + where any catalog it created
    might live — repo-local in the project, or a NAMED catalog in xorq's HOME-keyed store
    (``$HOME/.local/share/xorq/catalogs``). The model is free to choose (the skill's non-interactive
    default is a named catalog), so we discover both. ``home`` is the HOME the agent ran under: a
    per-test temp dir when isolated (named store starts empty → every catalog is this run's), else
    the real HOME (``named_before`` records what pre-existed so we only count this run's catalogs)."""

    result: dict
    project: Path
    home: Path
    named_before: frozenset = frozenset()
    isolated_home: bool = False

    @property
    def all_catalog_dirs(self) -> list:
        """Every catalog this run could have produced: repo-local catalogs in the project plus
        named catalogs in this run's HOME store (scoped to those created during the run)."""
        dirs = set(_catalog_dirs(self.project)) if self.project.exists() else set()
        store = self.home / ".local" / "share" / "xorq" / "catalogs"
        if store.exists():
            for d in store.iterdir():
                if (d / "catalog.yaml").exists() and (self.isolated_home or d.name not in self.named_before):
                    dirs.add(d)
        return sorted(dirs)

    @property
    def is_error(self) -> bool:
        return bool(self.result.get("is_error"))

    @property
    def said(self) -> str:
        prefix = "[session ended on is_error] " if self.is_error else ""
        return prefix + str(self.result.get("result", ""))

    @property
    def answer(self) -> str:
        """The model's full final text (untruncated) — what a read-only skill is judged on."""
        return str(self.result.get("result", ""))


@pytest.fixture(scope="session")
def claude_bin() -> str:
    binp = shutil.which("claude")
    if not binp:
        pytest.skip("claude CLI not found (npm install -g @anthropic-ai/claude-code)")
    return binp


@pytest.fixture(scope="session")
def claude_auth() -> None:
    """Skip the LLM suite when headless claude has no way to authenticate."""
    has_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if not has_key and not (Path.home() / ".claude").exists():
        pytest.skip(
            "no claude auth (set ANTHROPIC_API_KEY or CLAUDE_CODE_OAUTH_TOKEN, or log in — ~/.claude missing)"
        )


@pytest.fixture
def claude_project(tmp_path: Path) -> Path:
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


def seed_files(proj: Path, *names: str) -> Path:
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


def _seed(xorq_bin: str, proj: Path, *args: object) -> subprocess.CompletedProcess:
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


def seed_catalog_sources(xorq_bin: str, proj: Path, names: Iterable[str]) -> Path:
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
def run_claude(
    claude_bin: str, claude_auth: object, claude_project: Path, tmp_path: Path
) -> Iterator[Callable[..., ClaudeRun]]:
    """Returns ``run(prompt, *, env_extra=None, timeout=300) -> ClaudeRun``.

    Each call runs ``claude -p`` headless with the xorq plugin loaded, in the temp project (cwd);
    the plugin's SessionStart hook injects the shared essentials (no ambient CLAUDE.md).

    Per-test catalog isolation lets the model do the REALISTIC thing — create a NAMED catalog (the
    skill's non-interactive default), which xorq keeps in ``$HOME/.local/share/xorq/catalogs``. To
    isolate that shared HOME-keyed store we redirect HOME to a per-test temp dir — but only when
    claude auth is token-based (``CLAUDE_CODE_OAUTH_TOKEN`` / ``ANTHROPIC_API_KEY``, both
    HOME-independent), as in CI. With a redirected HOME each test gets its own empty named store, so
    named-catalog creation can't collide under ``pytest -n``. When auth is a local OAuth login (in
    ``~/.claude``), redirecting HOME would break it, so we keep the real HOME and instead reap the
    catalogs this test created on teardown. Cache is a unique ``XORQ_CACHE_DIR`` either way.
    """
    append = STEER  # plugin ships no CLAUDE.md; skills inject the shared essentials themselves
    # Optionally pin the model (e.g. XORQ_CLAUDE_PLUGIN_TEST_MODEL=sonnet to run the
    # Opus-slow llm suite faster/cheaper). Unset -> claude's session default.
    model = os.environ.get("XORQ_CLAUDE_PLUGIN_TEST_MODEL")
    cache_dir = tempfile.mkdtemp(prefix="xorq-test-cache-", dir="/tmp")

    # Token auth is HOME-independent → safe to redirect HOME (full per-test named-store isolation).
    # OAuth login lives in ~/.claude → keep the real HOME and reap this test's catalogs on teardown.
    token_auth = bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"))
    real_cache = Path.home() / ".cache"  # keep uv's warm cache reachable across a HOME redirect
    if token_auth:
        home = tmp_path / "home"
        home.mkdir()
        (home / ".gitconfig").write_text("[user]\n\tname = xorq e2e\n\temail = e2e@xorq.dev\n")
        named_before: frozenset = frozenset()
    else:
        home = Path.home()
        store = home / ".local" / "share" / "xorq" / "catalogs"
        named_before = frozenset(p.name for p in store.iterdir() if p.is_dir()) if store.exists() else frozenset()
    xdg_config = tmp_path / "xdg-config"
    xdg_config.mkdir()

    runs = {"n": 0}

    def _run(prompt, *, env_extra=None, timeout=420) -> ClaudeRun:
        env = os.environ.copy()
        env.pop("XORQ_DEFAULT_CATALOG", None)
        env["XORQ_CACHE_DIR"] = cache_dir
        env["XDG_CONFIG_HOME"] = str(xdg_config)
        if token_auth:  # isolated per-test HOME → named catalogs land under it, never the real store
            env["HOME"] = str(home)
            env["XDG_DATA_HOME"] = str(home / ".local" / "share")
            env["XDG_CONFIG_HOME"] = str(home / ".config")
            # but keep uv's package cache on the real (warm) location, else every test re-downloads
            # the venv deps under the temp HOME and the -n 4 CI suite blows its time budget.
            env["UV_CACHE_DIR"] = str(real_cache / "uv")
        if env_extra:
            env.update(env_extra)
        # Stream events (`--verbose --output-format stream-json`) to a per-call log instead
        # of buffering one final JSON blob. The plain `json` format emits nothing until the
        # session ends, so a timeout leaves us with empty stdout and zero insight into where
        # it hung; streaming to a file means a timeout can surface the last events it managed.
        runs["n"] += 1
        log_path = Path(cache_dir) / f"claude-stream-{runs['n']}.jsonl"
        argv = [
            claude_bin, "-p", str(prompt),
            "--plugin-dir", str(PLUGIN_DIR),
            "--verbose",
            "--output-format", "stream-json",
            # Grant the tools the ingest skill needs (Skill to load it, Bash to drive xorq,
            # Write for the ingest script) rather than bypassing permissions wholesale.
            "--allowedTools", "Bash Edit Write Read Glob Grep Skill",
            "--append-system-prompt", append,
        ]
        if model:
            argv += ["--model", model]
        with open(log_path, "w") as logf:
            try:
                proc = subprocess.run(
                    argv, cwd=str(claude_project), env=env,
                    stdout=logf, stderr=subprocess.PIPE, text=True, timeout=timeout,
                )
            except subprocess.TimeoutExpired as e:
                tail = _stream_tail(log_path, 40)
                pytest.fail(
                    f"claude -p timed out after {timeout}s\n"
                    f"--- last stream events ({log_path.name}) ---\n{tail}\n"
                    f"--- stderr ---\n{(e.stderr or '')[-2000:]}"
                )
        # NB: we deliberately do NOT assert proc.returncode == 0. A session can end on a
        # transient `is_error` (e.g. "API Error: socket connection closed") *after* the
        # skill already produced the catalog. The artifact is the source of truth, so let
        # the outcome assertions judge; the error text rides along in ClaudeRun.said for
        # legible failures when no catalog was in fact created.
        result = _stream_result(log_path)
        if result is None:
            pytest.fail(
                "claude stream had no result event "
                f"(exit {proc.returncode}):\n{_stream_tail(log_path, 40)}\n{(proc.stderr or '')[-2000:]}"
            )
        return ClaudeRun(result=result, project=claude_project, home=home,
                         named_before=named_before, isolated_home=token_auth)

    yield _run
    shutil.rmtree(cache_dir, ignore_errors=True)
    # Isolated (temp) HOME is auto-cleaned with tmp_path. On the real HOME, reap only the named
    # catalogs this test created (anything new since `named_before`) so the store doesn't accumulate.
    if not token_auth:
        store = home / ".local" / "share" / "xorq" / "catalogs"
        if store.exists():
            for d in store.iterdir():
                if d.is_dir() and d.name not in named_before and (d / "catalog.yaml").exists():
                    shutil.rmtree(d, ignore_errors=True)


# --- outcome assertions: verify the deterministic artifacts the skill produced ---

_CAT_EXCLUDE = (".venv", "__pycache__", ".git", "entries", "aliases")


def _catalog_dirs(root: Path) -> list:
    out = []
    for p in root.rglob("catalog.yaml"):
        if any(part in _CAT_EXCLUDE or part.startswith("builds") for part in p.parts):
            continue
        out.append(p.parent)
    return out


def _xq(xorq_bin: str, *args: object, timeout: int = 180) -> subprocess.CompletedProcess:
    return subprocess.run([xorq_bin, *args], capture_output=True, text=True, timeout=timeout)


def catalog_kinds(xorq_bin: str, cat: Path) -> str:
    """stdout of ``xorq catalog -p <cat> list --kind`` (rows look like ``<hash>\\tsource``)."""
    return _xq(xorq_bin, "catalog", "-p", str(cat), "list", "--kind").stdout


def source_hashes(xorq_bin: str, cat: Path) -> list:
    """Content hashes of every ``source`` entry in a catalog (``list`` shows hashes)."""
    out = []
    for line in catalog_kinds(xorq_bin, cat).splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] == "source":
            out.append(parts[0])
    return out


def source_entries(xorq_bin: str, run: ClaudeRun) -> list:
    """Every (catalog, hash) source entry the model created, across all places a
    catalog might land (repo-local in the project, or named under the isolated XDG home)."""
    cats = run.all_catalog_dirs
    return [(cat, h) for cat in cats for h in source_hashes(xorq_bin, cat)]


def entry_schema(xorq_bin: str, cat: Path, ident: str) -> dict:
    """The stored ``schema_out`` ({name: type}) of a catalog entry — metadata, no execution."""
    return json.loads(_xq(xorq_bin, "catalog", "-p", str(cat), "schema", ident, "--json").stdout)["schema_out"]


def catalog_run_rows(xorq_bin: str, cat: Path, ident: str, limit: int = 3) -> list:
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
    path = Path(path)
    read = xo.deferred_read_parquet if path.suffix == ".parquet" else xo.deferred_read_csv
    return {n: str(t) for n, t in read(str(path.resolve())).schema().items()}


def _canon(schema: dict, types: bool) -> tuple:
    return tuple(sorted(schema.items())) if types else tuple(sorted(schema))


def assert_sources(
    xorq_bin: str, run: ClaudeRun, expected_schemas: list, *, types: bool = True, run_one: bool = True
) -> None:
    """Assert the model produced exactly one source entry per expected schema.

    ``expected_schemas`` is a list of {name: type} dicts (one per file the prompt implies);
    the match is a MULTISET over ``schema_out`` so schema-identical files (events_dev /
    events_prod) require two distinct entries, not one. ``types=False`` compares column
    names only (for DB-backed sources whose types vary by backend). ``run_one`` also
    executes one entry to prove the pipeline runs (skip where ``catalog run`` can't, e.g.
    materialized DuckDB in 0.3.29).
    """
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
        # run_entry_rows: bare run for a deferred source, extract+xorq-run for a materialized one.
        assert run_entry_rows(xorq_bin, cat, h, limit=3), \
            f"source {h} produced no rows\nclaude said: {run.said}"


# --- composer outcome assertions: derived (composed/expr) entries + flexible value checks ---


def derived_entries(xorq_bin: str, run: ClaudeRun) -> list:
    """(catalog, hash) for every ``composed`` or ``expr`` entry the model created.

    Either kind counts: single-source shaping lands as ``composed``; a multi-source join
    (compose is single-input) lands as ``expr``. Source entries (the seeded inputs) are
    excluded — we only want the derived results.
    """
    out = []
    cats = run.all_catalog_dirs
    for cat in cats:
        for line in catalog_kinds(xorq_bin, cat).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in ("composed", "expr"):
                out.append((cat, parts[0]))
    return out


def assert_grouped(rows: list, expected: dict, *, tol: float = 0.02) -> None:
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


def assert_argmax(rows: list, expected_top: object) -> None:
    """Assert the label with the largest numeric measure equals ``expected_top``.

    Robust to whether the model returned a sorted full table or just the top row, and to
    which measure it ranked by.
    """
    measures = [c for c in rows[0] if all(isinstance(r.get(c), (int, float)) and not isinstance(r.get(c), bool) for r in rows)]
    labels = [c for c in rows[0] if c not in measures]
    assert measures and labels, f"need a label + a measure column; cols={list(rows[0])}\nrows={rows}"
    cands = {str(max(rows, key=lambda r: r[m])[l]) for l in labels for m in measures}
    assert str(expected_top) in cands, f"expected top {expected_top!r}, argmax candidates {cands}\nrows={rows}"


def assert_derived(xorq_bin: str, run: ClaudeRun, check: Callable, *, limit: int = 60) -> None:
    """Find a derived entry whose run satisfies ``check(rows)``; fail with claude's words."""
    ent = derived_entries(xorq_bin, run)
    assert ent, f"no composed/expr entry created\nclaude said: {run.said}"
    last = None
    for cat, h in ent:
        # run_entry_rows tries bare ``catalog run`` first, then extract + ``xorq run`` — the
        # latter is what replays a derived entry whose plan bundles a materialized source or a
        # cross-backend transfer (bare run yields 0 rows for those on 0.3.30).
        rows = run_entry_rows(xorq_bin, cat, h, limit=limit)
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


def entries_by_kind(xorq_bin: str, run: ClaudeRun, kinds: Iterable[str]) -> list:
    """(catalog, hash) for every entry whose ``list --kind`` kind is in ``kinds``."""
    kinds = set(kinds)
    out = []
    cats = run.all_catalog_dirs
    for cat in cats:
        for line in catalog_kinds(xorq_bin, cat).splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[1] in kinds:
                out.append((cat, parts[0]))
    return out


def builder_entries(xorq_bin: str, run: ClaudeRun) -> list:
    """(catalog, hash) for every ``expr_builder`` entry the model created."""
    return entries_by_kind(xorq_bin, run, ("expr_builder",))


def assert_entry_runs(
    xorq_bin: str, run: ClaudeRun, check: Callable, *, kinds: Iterable[str], limit: int = 200
) -> None:
    """Find an entry of one of ``kinds`` whose run satisfies ``check(rows)``; fail with claude's words."""
    ents = entries_by_kind(xorq_bin, run, kinds)
    assert ents, f"no entry of kinds {tuple(kinds)} created\nclaude said: {run.said}"
    last = None
    for cat, h in ents:
        # run_entry_rows (not bare catalog_run_rows): a builder over a cross-backend join
        # serializes a plan that bare ``catalog run`` can't replay (returns 0 rows on 0.3.30),
        # but extracting the build and ``xorq run``-ing it executes the plan in-process. The
        # fallback makes the run independent of the entry's source/join shape.
        rows = run_entry_rows(xorq_bin, cat, h, limit=limit)
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


def assert_answer_mentions(run: ClaudeRun, *needles: object, min_hits: int | None = None) -> None:
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


def catalog_snapshot(xorq_bin: str, cat: Path) -> tuple:
    """(entries, aliases) of a catalog — to prove a read-only skill mutated nothing."""
    kinds = tuple(sorted(catalog_kinds(xorq_bin, cat).splitlines()))
    aliases = tuple(sorted(_xq(xorq_bin, "catalog", "-p", str(cat), "list-aliases").stdout.splitlines()))
    return (kinds, aliases)


def assert_read_only(xorq_bin: str, cat: Path, before: tuple) -> None:
    """Assert the catalog's entry/alias set is identical to ``before`` (explore must not mutate)."""
    after = catalog_snapshot(xorq_bin, cat)
    assert after == before, (
        f"catalog changed — explore must be read-only\n  before: {before}\n  after:  {after}"
    )


# ---------------------------------------------------------------------------
# Integration e2e (multi-skill) — clone+compose (BTS), ingest+compose (penguins),
# ml x2 (iris). One casual prompt drives a whole flow; assertions run on the
# deterministic artifacts. These reuse the discovery/run helpers above and add:
#   - catalog lookup by NAME (penguins/iris) and by CONTENT (the BTS local copy),
#   - SQL introspection (stored `sql_queries`) for structural checks without executing, and
#   - model scoring via the builder round-trip (AUC / MSE) for the iris models.
# The penguins/iris CSVs are generated at runtime into the tmp project (iris from sklearn — offline;
# penguins from the pinned xorq example — needs the examples bucket), so nothing is committed.
#
# NB: a catalog the model names ("call it iris") lands in xorq's HOME-keyed named store; the
# run_claude fixture isolates that store per test (redirected HOME / reap-on-teardown), and
# ``ClaudeRun.all_catalog_dirs`` is the single discovery surface (project + this run's named store).
# ---------------------------------------------------------------------------


def github_reachable() -> bool:
    """True if a TCP connection to github.com:443 opens within 3s (gates the BTS test)."""
    try:
        with socket.create_connection(("github.com", 443), timeout=3):
            return True
    except OSError:
        return False


def seed_penguins(proj: Path) -> Path:
    """Write ``proj/data/penguins.csv`` from the pinned xorq example, returning its path.

    Generated at runtime (not committed) — the prompt references data/penguins.csv. Pulls from the
    examples bucket, so it skips the test if that data can't be fetched.
    """
    import xorq.examples as ex

    try:
        df = ex.penguins.fetch(backend=xo.connect()).execute()
    except Exception as e:  # network / examples bucket unavailable
        pytest.skip(f"penguins example data unavailable: {e}")
    data = proj / "data"
    data.mkdir(exist_ok=True)
    path = data / "penguins.csv"
    df.to_csv(path, index=False)
    return path


def seed_iris(proj: Path) -> Path:
    """Write ``proj/data/iris.csv`` from sklearn (bundled, offline), returning its path.

    Generated at runtime (not committed). Columns are normalized to snake_case and the integer
    target is mapped to species names, so the source is the familiar
    {sepal_length, sepal_width, petal_length, petal_width, species}.
    """
    from sklearn.datasets import load_iris

    d = load_iris(as_frame=True)
    df = d.frame.rename(columns={
        "sepal length (cm)": "sepal_length", "sepal width (cm)": "sepal_width",
        "petal length (cm)": "petal_length", "petal width (cm)": "petal_width",
    })
    df["species"] = df.pop("target").map(dict(enumerate(d.target_names)))
    data = proj / "data"
    data.mkdir(exist_ok=True)
    path = data / "iris.csv"
    df.to_csv(path, index=False)
    return path


def all_catalogs(run: ClaudeRun) -> list:
    """Every catalog the run could have produced — repo-local in the project or named in this run's
    HOME store (``ClaudeRun.all_catalog_dirs`` is the single, isolation-aware discovery surface)."""
    return run.all_catalog_dirs


def catalog_aliases(xorq_bin: str, cat: Path) -> list:
    """The alias names defined in a catalog (``list-aliases``)."""
    return [ln.strip() for ln in _xq(xorq_bin, "catalog", "-p", str(cat), "list-aliases").stdout.splitlines() if ln.strip()]


def named_catalog(run: ClaudeRun, *needles: str) -> Path | None:
    """The catalog whose directory name contains any ``needle`` (case-insensitive).

    The user names these catalogs ("call it iris"), so the model creates a catalog whose
    location carries that name — repo-local ``./iris`` or a named ``iris`` under XDG both match.
    """
    for cat in all_catalogs(run):
        low = cat.name.lower()
        if any(n.lower() in low for n in needles):
            return cat
    return None


def catalog_with_aliases(xorq_bin: str, run: ClaudeRun, *needles: str) -> Path | None:
    """The catalog that defines any of ``needles`` as an alias — identifies a catalog by its
    CONTENT rather than its name (the BTS local copy, whatever the model named the clone dir,
    is the one that carries ``flights`` / ``semantic-flights``)."""
    want = set(needles)
    for cat in all_catalogs(run):
        if want & set(catalog_aliases(xorq_bin, cat)):
            return cat
    return None


def bts_candidate_catalogs(run: ClaudeRun) -> list:
    """Catalogs that could hold the BTS local copy + the entries composed onto it.

    The model makes a *writable local copy* of the read-only remote — a repo-local catalog in the
    project or a named one in this run's store — and it need NOT carry the original
    `flights`/`semantic-flights` aliases (it may compose into a fresh catalog that references the
    cloned source), so callers discover by SIGNATURE. This is just all of this run's catalogs.
    """
    return run.all_catalog_dirs


def catalog_entries(xorq_bin: str, cat: Path) -> list:
    """(hash, kind) for every entry in a catalog (``list --kind`` rows: ``<hash>\\t<kind>``)."""
    out = []
    for line in catalog_kinds(xorq_bin, cat).splitlines():
        parts = line.split()
        if len(parts) >= 2:
            out.append((parts[0], parts[1]))
    return out


def derived_in(xorq_bin: str, cat: Path) -> list:
    """Hashes of the ``composed`` / ``expr`` (derived) entries in one catalog."""
    return [h for h, k in catalog_entries(xorq_bin, cat) if k in ("composed", "expr")]


def derived_rows(xorq_bin: str, cat: Path, *, limit: int = 400) -> list:
    """Every row from running all derived entries in a catalog (union across entries).

    The model may answer a two-part question ("heaviest AND tiniest") in one entry or split it
    across two, so callers check the union rather than any single entry's rows.
    """
    rows = []
    for h in derived_in(xorq_bin, cat):
        rows += run_entry_rows(xorq_bin, cat, h, limit=limit)  # bare run, then extract+xorq-run fallback
    return rows


def entry_sql(xorq_bin: str, cat: Path, ident: str) -> str:
    """The entry's stored ``sql_queries`` concatenated and lowercased (``schema --json``).

    Lets a test prove an expression's STRUCTURE — which columns/filters it encodes — without
    executing it (so the BTS expressions, whose data isn't cached, are still checkable). Empty
    string for entries that expose no SQL (e.g. a plain source).
    """
    raw = _xq(xorq_bin, "catalog", "-p", str(cat), "schema", str(ident), "--json").stdout
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    parts = []
    for q in d.get("sql_queries") or []:
        parts += [str(x) for x in q] if isinstance(q, (list, tuple)) else [str(q)]
    return "\n".join(parts).lower()


def run_entry_rows(xorq_bin: str, cat: Path, ident: str, *, limit: int = 200) -> list:
    """Run a catalog entry and return its JSON rows, robust to BOTH source forms.

    Tries ``catalog run`` first (resolves a deferred / re-readable source), then falls back to
    extracting the build archive and ``xorq run``-ing the build dir — which is what works for a
    MATERIALIZED read_parquet source, whose bundled relative ``database_tables/*.parquet`` only
    resolves inside the extracted build (``catalog run`` returns 0 rows for those in 0.3.29). The two
    paths are complementary, so trying both makes the run independent of how the model ingested.
    Accepts an alias (resolved via ``aliases/<name>.zip``) or a content hash; ``[]`` if neither works.
    """
    rows = catalog_run_rows(xorq_bin, cat, ident, limit=limit)
    if rows:
        return rows
    cat = Path(cat)
    archive = cat / "aliases" / f"{ident}.zip"
    if not archive.exists():
        archive = cat / "entries" / f"{ident}.zip"
    if not archive.exists():
        return []
    tmp = Path(tempfile.mkdtemp(prefix="xorq-entry-"))
    try:
        with zipfile.ZipFile(archive.resolve()) as zf:
            zf.extractall(tmp)
        builds = [p.parent for p in tmp.rglob("expr.yaml")]
        if not builds:
            return []
        r = subprocess.run(
            [xorq_bin, "run", str(builds[0]), "-o", "-", "-f", "json", "--limit", str(limit)],
            capture_output=True, text=True, timeout=300,
        )
        return [json.loads(ln) for ln in r.stdout.splitlines() if ln.strip().startswith("{")]
    except (zipfile.BadZipFile, subprocess.TimeoutExpired):
        return []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def entry_show(xorq_bin: str, cat: Path, ident: str) -> str:
    """An entry's ``catalog show`` text, lowercased — used to assert a builder's kind.

    For a fitted-pipeline expr_builder this reports ``Type: Expression Builder`` plus a
    ``Type: fitted_pipeline`` block (steps + target), so a test can confirm the model is a
    FittedPipeline (not just any builder) — the same string the ml skill's Verify step checks.
    """
    return _xq(xorq_bin, "catalog", "-p", str(cat), "show", str(ident)).stdout.lower()


def builder_metric(
    xorq_bin: str, cat: Path, ident: str, data_csv: Path, scorer: str
) -> float | None:
    """Recover a FittedPipeline from a catalogued entry and score it on ``data_csv``.

    Runs the skill's documented round-trip (``run -c 'source.ls.builder.score_expr(...)'``) with
    an sklearn ``scorer`` name and returns the scalar metric. Returns ``None`` when the scorer
    doesn't apply to that model (a regression scorer on a classifier errors to no rows, and vice
    versa) — which is exactly how a caller tells the classifier and the regressor apart.
    """
    code = (
        f"source.ls.builder.score_expr("
        f"xo.deferred_read_csv({str(Path(data_csv).resolve())!r}), scorer={scorer!r})"
    )
    r = _xq(xorq_bin, "catalog", "-p", str(cat), "run", str(ident),
            "--use-this-venv", "-c", code, "-o", "-", "-f", "json")
    for line in r.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        nums = [v for v in row.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
        if nums:
            return float(nums[0])
    return None
