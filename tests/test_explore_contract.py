"""Contract tests for the `catalog-explore` skill's read-only vocabulary and the kernel's
`run` pitfalls (xorq/skills/catalog-explore/SKILL.md + skills/_shared/kernel.md).

Drift alarm, NOT a re-test of xorq: pins exactly the call-outs those docs make, so a version
bump that changes them fails here and the docs must be updated.

Pins:
  - hash vs alias  -> `show` / `schema` / `run` accept EITHER (explore: "Entries are content
                      hashes; aliases are the names that point at them")
  - `schema --json`-> parses as JSON carrying `schema_out` (the VERIFY schema check)
  - `check` / `log`-> `check` validates ("OK"); `log` shows the replay ops (`add`)
  - kernel pitfall -> `-o -` is REQUIRED for `run` previews; without it output goes to
                      /dev/null (no rows on stdout)
  - kernel pitfall -> `-c` is sandboxed: no imports, no builtins, no dunders

Source data is tests/data/customers.csv; row checks use `--use-this-venv` (offline).
Verified against xorq 0.3.29.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "tests" / "data" / "customers.csv"


def _run(xorq_bin: str, *args: object, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [xorq_bin, *map(str, args)], capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(scope="module")
def explore_cat(xorq_bin: str, tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """A catalog with one aliased `customers` source entry; returns (catalog, hash, alias)."""
    if not CSV.exists():
        pytest.skip("missing tests/data/customers.csv")
    tmp = tmp_path_factory.mktemp("explore")
    (tmp / "ingest.py").write_text(f"import xorq.api as xo\nexpr = xo.deferred_read_csv({str(CSV)!r})\n")
    bp = tmp / "bp.txt"
    r = _run(xorq_bin, "build", tmp / "ingest.py",
             "--builds-dir", tmp / "builds_source", "--emit-build-path-to", bp)
    assert r.returncode == 0, r.stderr
    cat = tmp / "cat"
    _run(xorq_bin, "catalog", "-p", cat, "init")
    r = _run(xorq_bin, "catalog", "-p", cat, "add", bp.read_text().strip(), "-a", "customers")
    assert r.returncode == 0, r.stdout + r.stderr
    entry_hash = _run(xorq_bin, "catalog", "-p", cat, "list").stdout.split()[0]
    return cat, entry_hash, "customers"


def test_show_and_schema_accept_hash_or_alias(xorq_bin: str, explore_cat: tuple) -> None:
    """Explore §2/§3: `list` shows hashes, `list-aliases` shows handles, and `show` / `schema`
    accept EITHER identifier."""
    cat, entry_hash, alias = explore_cat
    assert alias in _run(xorq_bin, "catalog", "-p", cat, "list-aliases").stdout
    for ident in (entry_hash, alias):
        assert _run(xorq_bin, "catalog", "-p", cat, "show", ident).returncode == 0, ident
        assert _run(xorq_bin, "catalog", "-p", cat, "schema", ident).returncode == 0, ident


def test_schema_json_carries_schema_out(xorq_bin: str, explore_cat: tuple) -> None:
    """VERIFY: `schema <alias> --json` is machine-readable and carries `schema_out`."""
    cat, _, alias = explore_cat
    r = _run(xorq_bin, "catalog", "-p", cat, "schema", alias, "--json")
    assert r.returncode == 0, r.stderr
    meta = json.loads(r.stdout)
    assert "customer_id" in meta["schema_out"], meta


def test_check_ok_and_log_shows_ops(xorq_bin: str, explore_cat: tuple) -> None:
    """Explore §5: `check` validates consistency -> "OK"; `log` shows the replay ops."""
    cat, _, _ = explore_cat
    r = _run(xorq_bin, "catalog", "-p", cat, "check")
    assert r.returncode == 0 and "OK" in r.stdout, r.stdout + r.stderr
    log = _run(xorq_bin, "catalog", "-p", cat, "log").stdout
    assert "add" in log, log


def test_run_without_dash_o_emits_no_rows(xorq_bin: str, explore_cat: tuple) -> None:
    """Kernel pitfall: `-o -` is REQUIRED for previews — without it, output defaults to
    /dev/null and no rows reach stdout."""
    cat, _, alias = explore_cat
    base = ("catalog", "-p", cat, "run", alias, "--use-this-venv", "-f", "json", "--limit", "3")
    silent = _run(xorq_bin, *base)
    assert silent.returncode == 0, silent.stderr
    assert not [l for l in silent.stdout.splitlines() if l.strip().startswith("{")], silent.stdout
    loud = _run(xorq_bin, *base, "-o", "-")
    assert [l for l in loud.stdout.splitlines() if l.strip().startswith("{")], loud.stdout + loud.stderr


def test_c_namespace_is_sandboxed(xorq_bin: str, explore_cat: tuple) -> None:
    """Kernel pitfall: `-c` sees only `source` / `xo` / `ibis` — no builtins, no imports,
    no dunders."""
    cat, _, alias = explore_cat
    for code in ('__import__("os").getcwd()', 'open("/etc/hostname")'):
        r = _run(xorq_bin, "catalog", "-p", cat, "run", alias, "--use-this-venv",
                 "-c", code, "-o", "-", "-f", "json", "--limit", "1")
        assert r.returncode != 0, f"sandbox should reject {code!r}\n{r.stdout}"
