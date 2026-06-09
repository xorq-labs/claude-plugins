"""LLM end-to-end tests for the `ingest` skill (xorq/skills/ingest/SKILL.md).

Unlike the deterministic contract tests (which call the CLI directly to pin what the
skill *documents*), these drive a real headless ``claude -p`` session — with the xorq
plugin loaded via ``--plugin-dir`` and xorq/CLAUDE.md injected — using short, casual,
user-voice prompts (tests/prompts.py). They verify the skill can ACT on a vague request.

A model run is non-deterministic, so assertions are on the deterministic artifacts it
leaves behind. The contract verified is strong: every data source the prompt implies must
become its OWN source entry whose ``schema_out`` matches that file's real schema — derived
from the fixtures at runtime (no hardcoded drift) and matched as a MULTISET, so the
schema-identical events_dev/events_prod files require two distinct entries. The model picks
aliases/paths/build dirs; the conftest helpers discover entries and run by content-hash.

Opt-in only: ``pytest -m llm``. Skips cleanly when ``claude`` isn't installed, when there's
no auth (ANTHROPIC_API_KEY / ~/.claude), or when the fixture data is absent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import (
    DATA,
    POSTGRES_ENV,
    assert_sources,
    file_schema,
    postgres_reachable,
    seed_duckdb,
    seed_files,
    seed_sqlite,
)
from prompts import IngestData

pytestmark = pytest.mark.llm

CSV_FILES = ["customers.csv", "products.csv", "transactions.csv"]
PARQUET_FILES = ["events_dev.parquet", "events_prod.parquet"]
ALL_FILES = CSV_FILES + PARQUET_FILES


def test_llm_ingest_raw(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """"load my raw data" -> EVERY file present is its own runnable source entry.

    The project is seeded with exactly the 5 data files (3 CSV + 2 parquet, no
    app.db/warehouse.duckdb), so "all my raw data" maps to precisely those 5 — one source
    entry each, matched by schema. This covers both the CSV and Parquet ingest paths (so
    there's no separate parquet test).
    """
    seed_files(claude_project, *ALL_FILES)
    run = run_claude(IngestData.RAW)
    assert_sources(xorq_bin, run, [file_schema(DATA / f) for f in ALL_FILES])


def test_llm_ingest_sqlite(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """Only a sqlite db present -> one source = the customers table. Types vary by backend,
    so match column names only."""
    seed_sqlite(claude_project)
    run = run_claude(IngestData.SQLITE)
    assert_sources(xorq_bin, run, [file_schema(DATA / "customers.csv")], types=False)


def test_llm_ingest_duckdb(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    pytest.importorskip("duckdb")
    seed_duckdb(claude_project)
    run = run_claude(IngestData.DUCKDB)
    # `catalog run` can't locate a materialized DuckDB entry's parquet in 0.3.29, so verify
    # the source + its schema only (don't execute it).
    assert_sources(xorq_bin, run, [file_schema(DATA / "customers.csv")], types=False, run_one=False)


@pytest.mark.skipif(not postgres_reachable(), reason="no reachable postgres (docker compose up -d)")
def test_llm_ingest_postgres(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    # Nothing seeded locally — the model connects to the server via the ${POSTGRES_*} profile.
    run = run_claude(IngestData.POSTGRES, env_extra=POSTGRES_ENV)
    # Running would need the live server in the runner env; assert the source + its schema.
    assert_sources(xorq_bin, run, [file_schema(DATA / "customers.csv")], types=False, run_one=False)
