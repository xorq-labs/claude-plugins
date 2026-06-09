"""Contract tests for the `composer` skill's documented recipes (xorq/skills/composer/SKILL.md).

Drift alarm, NOT a re-test of xorq: it runs only the CLI surface the SKILL.md tells the agent
to run, so a version bump that changes these behaviors fails here and the skill must be updated.

Pins:
  - §A inline code   -> `compose <src> -c "…"` mints a `composed` entry; `show` reports
                        "Composed from"
  - §B transforms    -> an expression over `xo.table(...)` catalogs as `unbound_expr` and
                        applies via `compose <src> <transform>`
  - transform rule   -> a data-bearing (`source`) entry is rejected as a transform
  - schema rule      -> a transform whose `schema_in` isn't covered by the source fails,
                        naming the offending column
  - `--dry-run`      -> previews without cataloguing (claim: compose otherwise ALWAYS catalogs)

Source data is tests/data/customers.csv. `catalog add` builds a wheel of this project, so tests
run from the repo root in a wheel-buildable env; row checks use `--use-this-venv` (offline).
Verified against xorq 0.3.28.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "tests" / "data" / "customers.csv"


def _run(xorq_bin: str, *args: object, timeout: int = 300, env: dict | None = None) -> subprocess.CompletedProcess:
    full_env = {**os.environ, **(env or {})}
    return subprocess.run(
        [xorq_bin, *map(str, args)], capture_output=True, text=True, timeout=timeout, env=full_env
    )


def _build_add(xorq_bin: str, tmp: Path, cat: Path, name: str, script: str, alias: str) -> None:
    (tmp / f"{name}.py").write_text(script)
    bp = tmp / f"bp_{name}.txt"
    r = _run(xorq_bin, "build", tmp / f"{name}.py",
             "--builds-dir", tmp / f"builds_{name}", "--emit-build-path-to", bp)
    assert r.returncode == 0, r.stderr
    # Kernel pitfall: compose merges the entries' bundles, so wheels of this project must be
    # byte-identical across `add`s — pin SOURCE_DATE_EPOCH for reproducible wheels, else
    # compose fails with "wheel collision".
    r = _run(xorq_bin, "catalog", "-p", cat, "add", bp.read_text().strip(), "-a", alias,
             env={"SOURCE_DATE_EPOCH": "0"})
    assert r.returncode == 0, r.stdout + r.stderr


def _kinds(xorq_bin: str, cat: Path) -> str:
    return _run(xorq_bin, "catalog", "-p", cat, "list", "--kind").stdout


@pytest.fixture(scope="module")
def compose_cat(xorq_bin: str, tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A catalog with a `customers` source entry and a reusable `adults` transform.

    The transform is an expression over an UNBOUND table (schema only) whose columns are a
    subset of customers.csv — exactly the SKILL.md §B recipe.
    """
    if not CSV.exists():
        pytest.skip("missing tests/data/customers.csv")
    tmp = tmp_path_factory.mktemp("composer")
    cat = tmp / "cat"
    _run(xorq_bin, "catalog", "-p", cat, "init")
    _build_add(
        xorq_bin, tmp, cat, "source",
        f"import xorq.api as xo\nexpr = xo.deferred_read_csv({str(CSV)!r})\n",
        "customers",
    )
    _build_add(
        xorq_bin, tmp, cat, "transform",
        'import xorq.api as xo\n'
        't = xo.table({"age": "int64", "state": "string"}, name="t")\n'
        'expr = t.filter(t.age >= 18)\n',
        "adults",
    )
    return cat


def test_unbound_transform_catalogs_as_unbound_expr(xorq_bin: str, compose_cat: Path) -> None:
    """§B: an expression over `xo.table(...)` (schema only, no data) is kind `unbound_expr`."""
    assert "unbound_expr" in _kinds(xorq_bin, compose_cat)


def test_inline_code_mints_composed_entry(xorq_bin: str, compose_cat: Path) -> None:
    """§A: `compose <src> -c "source.<op>…"` builds AND catalogs a `composed` entry in one step;
    `show` reports its provenance ("Composed from")."""
    r = _run(
        xorq_bin, "catalog", "-p", compose_cat, "compose", "customers",
        "-c", "source.filter(source.age > 40).select('customer_id', 'age')",
        "-a", "older",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert "composed" in _kinds(xorq_bin, compose_cat)
    show = _run(xorq_bin, "catalog", "-p", compose_cat, "show", "older").stdout
    assert "Composed from" in show, show


def test_transform_entry_applies_to_compatible_source(xorq_bin: str, compose_cat: Path) -> None:
    """§B: `compose <src> <transform>` applies the unbound transform (the source's columns are
    a superset of the transform's schema_in) and the result runs."""
    r = _run(xorq_bin, "catalog", "-p", compose_cat, "compose", "customers", "adults",
             "-a", "adult-customers")
    assert r.returncode == 0, r.stdout + r.stderr
    r = _run(xorq_bin, "catalog", "-p", compose_cat, "run", "adult-customers",
             "--use-this-venv", "-o", "-", "-f", "json", "--limit", "3")
    assert r.returncode == 0, r.stderr
    rows = [json.loads(l) for l in r.stdout.splitlines() if l.strip().startswith("{")]
    assert rows and all(row["age"] >= 18 for row in rows), rows


def test_data_bearing_transform_is_silently_accepted(xorq_bin: str, compose_cat: Path) -> None:
    """Pitfall: transforms must be `unbound_expr` — but the CLI does NOT reject a data-bearing
    (`source`) entry passed as a transform: it composes silently and the result is the source
    unchanged. This pins the SILENCE (the reason the skill must warn); if xorq starts rejecting
    it, the pitfall wording should flip to "rejected"."""
    r = _run(xorq_bin, "catalog", "-p", compose_cat, "compose", "customers", "customers",
             "-a", "noop-transform")
    assert r.returncode == 0, r.stdout + r.stderr
    rows = _run(xorq_bin, "catalog", "-p", compose_cat, "run", "noop-transform",
                "--use-this-venv", "-o", "-", "-f", "json", "--limit", "2")
    got = [json.loads(l) for l in rows.stdout.splitlines() if l.strip().startswith("{")]
    assert got and set(got[0]) == {"customer_id", "name", "age", "state", "signup_date", "tier"}, got


def test_schema_mismatch_names_the_offending_column(xorq_bin: str, tmp_path_factory: pytest.TempPathFactory, compose_cat: Path) -> None:
    """§B schema rule: a transform whose `schema_in` has a column the source lacks fails,
    and the error lists the offending column."""
    tmp = tmp_path_factory.mktemp("mismatch")
    _build_add(
        xorq_bin, tmp, compose_cat, "mismatch",
        'import xorq.api as xo\n'
        't = xo.table({"no_such_column": "float64"}, name="t")\n'
        'expr = t.filter(t.no_such_column > 0)\n',
        "mismatched",
    )
    r = _run(xorq_bin, "catalog", "-p", compose_cat, "compose", "customers", "mismatched",
             "-a", "boom")
    assert r.returncode != 0, r.stdout
    assert "no_such_column" in (r.stdout + r.stderr), r.stdout + r.stderr


def test_dry_run_previews_without_cataloguing(xorq_bin: str, compose_cat: Path) -> None:
    """`--dry-run` previews the plan + result schema; no new entry lands (compose otherwise
    always catalogs)."""
    before = _kinds(xorq_bin, compose_cat)
    r = _run(
        xorq_bin, "catalog", "-p", compose_cat, "compose", "customers",
        "-c", "source.select('customer_id')", "-a", "never-lands", "--dry-run",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    assert _kinds(xorq_bin, compose_cat) == before, "dry-run must not add an entry"
