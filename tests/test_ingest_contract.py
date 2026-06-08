"""Contract tests for the `ingest` skill's build behaviors.

Pins what xorq/skills/ingest/SKILL.md claims, so doc drift is caught:
  - CSV / Parquet  -> `source` entry, runs and returns rows
  - SQLite table   -> `source` entry whose build embeds a sqlite `profiles.yaml`
  - DuckDB table   -> `source` entry whose build embeds a duckdb profile AND
                      materializes the table to database_tables/*.parquet
  - Postgres       -> the secure-secrets guarantee: a Profile stores `${ENV}`
                      references, never literal secrets (verified WITHOUT a server)

Source data is the real tests/data — single source of truth. The per-backend
source DBs (sqlite/duckdb) are built ONCE per session from the CSVs (no binary DB
fixtures committed; DuckDB's on-disk format is version-fragile). Each test skips
only on the file(s) it needs.

`catalog add` builds a wheel of this project, so tests run from the repo root
(pytest's rootdir) in a wheel-buildable env. Execution is checked with
`xorq run <build>` (in-process, offline) — not `catalog run` (isolated uv env, needs
the network). Verified against xorq 0.3.28.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import build_duckdb_db, build_sqlite_db

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"


def _need(*names):
    missing = [n for n in names if not (DATA / n).exists()]
    if missing:
        pytest.skip(f"missing tests/data: {', '.join(missing)}")


def _run(xorq_bin, *args):
    return subprocess.run([xorq_bin, *args], capture_output=True, text=True)


def _build(xorq_bin, tmp_path, script_src):
    """Write an ingest script, build it, return the build directory Path."""
    (tmp_path / "ingest.py").write_text(script_src)
    bp = tmp_path / "bp.txt"
    r = _run(
        xorq_bin, "build", str(tmp_path / "ingest.py"),
        "--builds-dir", str(tmp_path / "builds_source"),
        "--emit-build-path-to", str(bp),
    )
    assert r.returncode == 0, r.stderr
    return Path(bp.read_text().strip())


def _add(xorq_bin, tmp_path, build_path, alias):
    cat = tmp_path / "cat"
    if not (cat / "catalog.yaml").exists():
        _run(xorq_bin, "catalog", "-p", str(cat), "init")
    r = _run(xorq_bin, "catalog", "-p", str(cat), "add", str(build_path), "-a", alias)
    assert r.returncode == 0, r.stdout + r.stderr
    return cat


def _kinds(xorq_bin, cat):
    return _run(xorq_bin, "catalog", "-p", str(cat), "list", "--kind").stdout


def _rows(xorq_bin, build_path, limit=3):
    r = _run(xorq_bin, "run", str(build_path), "-o", "-", "-f", "json", "--limit", str(limit))
    assert r.returncode == 0, r.stderr
    return [json.loads(line) for line in r.stdout.splitlines() if line.strip().startswith("{")]


# --- source DBs built ONCE per session from the CSVs (single source of truth) ---

@pytest.fixture(scope="session")
def sqlite_db(tmp_path_factory):
    _need("customers.csv")
    return build_sqlite_db(tmp_path_factory.mktemp("sqlite") / "app.db")


@pytest.fixture(scope="session")
def duckdb_db(tmp_path_factory):
    pytest.importorskip("duckdb")
    _need("customers.csv")
    return build_duckdb_db(tmp_path_factory.mktemp("duckdb") / "warehouse.duckdb")


def test_ingest_csv_creates_source_and_runs(xorq_bin, tmp_path):
    _need("customers.csv")
    build = _build(xorq_bin, tmp_path, f'import xorq.api as xo\nexpr = xo.deferred_read_csv({str(DATA / "customers.csv")!r})\n')
    cat = _add(xorq_bin, tmp_path, build, "customers_csv")
    assert "source" in _kinds(xorq_bin, cat)
    rows = _rows(xorq_bin, build)
    assert rows and "customer_id" in rows[0]


def test_ingest_parquet_creates_source_and_runs(xorq_bin, tmp_path):
    _need("events_dev.parquet")
    build = _build(xorq_bin, tmp_path, f'import xorq.api as xo\nexpr = xo.deferred_read_parquet({str(DATA / "events_dev.parquet")!r})\n')
    cat = _add(xorq_bin, tmp_path, build, "events_parquet")
    assert "source" in _kinds(xorq_bin, cat)
    rows = _rows(xorq_bin, build)
    assert rows and "event_id" in rows[0]


def test_ingest_sqlite_embeds_profile_and_runs(xorq_bin, tmp_path, sqlite_db):
    build = _build(
        xorq_bin, tmp_path,
        f'import xorq.api as xo\ncon = xo.sqlite.connect({str(sqlite_db)!r})\nexpr = con.table("customers")\n',
    )
    # the connection is embedded as a profile, not a copy of the data
    assert "con_name: sqlite" in (build / "profiles.yaml").read_text()
    cat = _add(xorq_bin, tmp_path, build, "customers_sqlite")
    assert "source" in _kinds(xorq_bin, cat)
    assert _rows(xorq_bin, build)[0]["customer_id"] == 1


def test_ingest_duckdb_materializes_and_runs(xorq_bin, tmp_path, duckdb_db):
    build = _build(
        xorq_bin, tmp_path,
        f'import xorq.api as xo\ncon = xo.duckdb.connect({str(duckdb_db)!r})\nexpr = con.table("customers")\n',
    )
    assert "con_name: duckdb" in (build / "profiles.yaml").read_text()
    # DuckDB is embedded -> the table is materialized into the build
    assert list((build / "database_tables").glob("*.parquet")), "expected materialized parquet"
    cat = _add(xorq_bin, tmp_path, build, "customers_duckdb")
    assert "source" in _kinds(xorq_bin, cat)
    assert _rows(xorq_bin, build)[0]["customer_id"] == 1


def test_postgres_profile_stores_env_refs_not_secrets():
    """The secure-secrets guarantee the skill relies on — no server needed."""
    from xorq.vendor.ibis.backends.profiles import Profile, check_for_exposed_secrets

    safe = Profile(con_name="postgres", kwargs_tuple=(
        ("host", "${POSTGRES_HOST}"), ("port", 5432), ("database", "xorq"),
        ("user", "${POSTGRES_USER}"), ("password", "${POSTGRES_PASSWORD}"),
    ))
    # the reference is stored verbatim, not resolved to the secret value
    assert safe.kwargs_dict["password"] == "${POSTGRES_PASSWORD}"
    # an env-ref profile passes the secret check
    check_for_exposed_secrets("postgres", safe.kwargs_dict)

    # a literal password is rejected, so it can never be committed into a catalog
    leaky = Profile(con_name="postgres", kwargs_tuple=(
        ("host", "h"), ("port", 5432), ("database", "xorq"),
        ("user", "u"), ("password", "hunter2"),
    ))
    with pytest.raises(ValueError):
        check_for_exposed_secrets("postgres", leaky.kwargs_dict)
