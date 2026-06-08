"""LLM end-to-end tests for the `builder` skill (xorq/skills/builder/SKILL.md).

Mirrors test_compose_skill_llm.py: a real headless ``claude -p`` session with the xorq plugin +
CLAUDE.md injected, driven by short user-voice prompts (tests/prompts.py). Setup is deterministic
— ``seed_catalog_sources`` seeds the source entries the prompt builds on (so we are NOT retesting
ingest); the model's job is to build / round-trip an ExprBuilder, and the assertions run the
artifact it produced.

Four builder instances, one machinery:
  - semantic model            -> spend by category                (expr_builder)
  - semantic model + join_one -> spend by customer tier           (expr_builder, table-prefixed)
  - fitted pipeline           -> train dev / recover / score prod  (expr_builder; FittedPipeline)
  - custom TagHandler         -> first-3 / recover / last-3 chars  (in-process round-trip)

Two deliberate assertion choices:
  - The ML test asserts SCHEMA + round-trip DETERMINISM, not accuracy. The events fixture has no
    signal for "is purchase" (a linear model can't beat ~0.58 AUC even on train; a flexible model
    overfits to 1.0 but is ~0.5 on prod), so an accuracy/AUC bar would reward overfitting and
    flake. Determinism instead proves the catalog round-trip reconstructs the same fitted model.
  - The custom builder's handler is registered in-process (no entry point), so its tag is
    decorative at ``catalog add`` time and the saved result may be a plain ``expr`` entry — the
    assertion checks the last-3 VALUES regardless of the entry's kind.

Opt-in only: ``pytest -m llm``. Skips cleanly without claude / auth / fixture data.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from conftest import (
    DATA,
    assert_entry_runs,
    assert_grouped,
    builder_entries,
    catalog_run_rows,
    entries_by_kind,
    seed_builder_module,
    seed_catalog_sources,
)
from prompts import Builder

pytestmark = pytest.mark.llm


def test_llm_builder_category_spend(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """Semantic model on transactions -> total spend per category (an expr_builder entry)."""
    seed_catalog_sources(xorq_bin, claude_project, ["transactions"])
    run = run_claude(Builder.CATEGORY_SPEND)
    expected = (
        pd.read_csv(DATA / "transactions.csv")
        .groupby("category").amount.sum().round(2).to_dict()
    )
    assert_entry_runs(
        xorq_bin, run, lambda rows: assert_grouped(rows, expected), kinds=("expr_builder",)
    )


def test_llm_builder_spend_by_tier_join(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """Semantic model with join_one (each transaction -> its one customer) -> spend per tier."""
    seed_catalog_sources(xorq_bin, claude_project, ["transactions", "customers"])
    run = run_claude(Builder.SPEND_BY_TIER)
    txn = pd.read_csv(DATA / "transactions.csv")
    cust = pd.read_csv(DATA / "customers.csv")
    expected = (
        txn.merge(cust[["customer_id", "tier"]], on="customer_id")
        .groupby("tier").amount.sum().round(2).to_dict()
    )
    assert_entry_runs(
        xorq_bin, run, lambda rows: assert_grouped(rows, expected), kinds=("expr_builder",)
    )


def _prediction_columns(row: dict) -> list:
    return [
        c for c in row
        if "predict" in c.lower() or c.lower() in ("prediction", "score", "label", "predicted", "proba")
    ]


def test_llm_builder_ml_predict(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """Fitted pipeline: train on dev events, catalog it, recover via .ls.builder, score prod.

    Schema + round-trip determinism (NOT accuracy — the fixture has no signal for "is purchase",
    so an AUC bar would reward overfitting and flake). We run the agent's OWN catalogued entries
    rather than re-deriving predict() on raw data: the agent's feature engineering lives in the
    expression graph (ibis), so a stored predict entry runs end-to-end while re-feeding raw events
    to ``.predict`` would miss those engineered columns. Asserts:
      - a fitted-pipeline expr_builder entry exists (the model round-trips through the catalog),
      - some catalogued entry runs to predictions (a prediction column alongside the event cols),
      - re-running it is byte-identical (deterministic — the same fitted model came back), and
      - a prod-sized (every-prod-row) prediction entry exists (prod was scored and saved).
    """
    seed_catalog_sources(xorq_bin, claude_project, ["events_dev", "events_prod"])
    run = run_claude(Builder.ML_PURCHASE, timeout=600)

    assert builder_entries(xorq_bin, run), (
        f"no fitted-pipeline (expr_builder) entry created\nclaude said: {run.said}"
    )

    n_prod = len(pd.read_parquet((DATA / "events_prod.parquet").resolve()))
    canon = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)  # order-insensitive

    # run the agent's stored entries directly (their graph already includes feature engineering)
    pred_entries = []
    for cat, h in entries_by_kind(xorq_bin, run, ("expr_builder", "expr", "composed", "source")):
        rows = catalog_run_rows(xorq_bin, cat, h, limit=n_prod + 50)
        if rows and _prediction_columns(rows[0]):
            pred_entries.append((cat, h, rows))
    assert pred_entries, f"no catalogued entry runs to predictions\nclaude said: {run.said}"

    # round-trip determinism: re-running a prediction entry reproduces it exactly
    cat, h, rows = pred_entries[0]
    assert canon(rows) == canon(catalog_run_rows(xorq_bin, cat, h, limit=n_prod + 50)), (
        f"prediction entry not deterministic across runs\nclaude said: {run.said}"
    )

    # prod inference was scored and saved: some prediction entry covers every prod event
    assert any(len(r) == n_prod for _, _, r in pred_entries), (
        f"no prod-sized ({n_prod}-row) prediction entry — was prod scored and saved?\n"
        f"claude said: {run.said}"
    )


def test_llm_builder_custom_textslice(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """Custom builder round-trip: build first-3 chars of product_id, recover the builder, do last-3.

    A trivial custom builder is PROVIDED (tests/data/slice_builder.py, seeded into the project) so
    the agent isn't authoring a TagHandler under time pressure — the e2e exercises the round-trip:
    build with one option (first-3), recover via .ls.builder, use the other option (last-3) to
    generate and add a new expr. The handler is in-process (no entry point), so the saved result
    may be a plain `expr` — assert the last-3 VALUES regardless of kind. last-3 of `PROD-0001` is
    `001`, disjoint from the original first-3 (all `PRO`), so a column whose values are all last-3
    codes proves the recovered builder produced the re-parameterized (last-3) transform.
    """
    seed_catalog_sources(xorq_bin, claude_project, ["products"])
    seed_builder_module(claude_project)
    run = run_claude(Builder.TEXT_SLICE, timeout=600)

    pids = pd.read_csv(DATA / "products.csv").product_id.astype(str)
    last3 = set(pids.str[-3:])  # e.g. {'001', '002', ...} — disjoint from first-3 {'PRO'}

    def check(rows):
        for c in rows[0]:
            vals = [str(r[c]) for r in rows if r.get(c) is not None]
            if vals and set(vals) <= last3 and len(set(vals)) > 1:
                return
        raise AssertionError(
            f"no column carries the last-3 chars of product_id; cols={list(rows[0])}\nrows={rows[:5]}"
        )

    assert_entry_runs(xorq_bin, run, check, kinds=("expr_builder", "expr", "composed"))
