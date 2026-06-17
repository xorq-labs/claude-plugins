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
    run_entry_rows,
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
    """Fitted pipeline round-trips through the catalog and runs to deterministic predictions.

    The prompt asks the agent to fit on dev events, catalog the model, load it back, and score
    prod. We assert the durable, agent-independent INVARIANT rather than a specific saved artifact:
    requiring a particular saved scored-prod entry was flaky because the agent varies run to run —
    it scores prod by recovering+printing (never persisting a re-runnable entry), or saves scored
    prod as a file rather than a catalog entry, or engineers features into the fit source (so a
    fixed raw-prod predict can't reproduce them). The one constant is the fitted-pipeline
    ``expr_builder`` entry, which IS ``fitted.predict(<train source>)`` — so running the entry
    re-executes predict with any feature engineering baked into the graph, needing no knowledge of
    the agent's features (verified: the entry runs to a prediction column, deterministically).
    NOT accuracy: the fixture has no real purchase signal, so an AUC bar would only reward
    overfitting and flake — deterministic score-on-held-out-data is covered by the iris e2e. Asserts:
      - a fitted-pipeline expr_builder entry exists (the model round-trips through the catalog),
      - running it yields a prediction column over its rows (the model actually predicts), and
      - re-running is byte-identical (deterministic — the same fitted model came back).
    """
    seed_catalog_sources(xorq_bin, claude_project, ["events_dev", "events_prod"])
    run = run_claude(Builder.ML_PURCHASE, timeout=900)

    builders = builder_entries(xorq_bin, run)
    assert builders, f"no fitted-pipeline (expr_builder) entry created\nclaude said: {run.said}"

    canon = lambda rows: sorted(json.dumps(r, sort_keys=True) for r in rows)  # order-insensitive

    # the fitted-pipeline entry IS fitted.predict(<train>); run_entry_rows executes it (extract +
    # xorq run for a materialized source), reproducing predict with the engineering in the graph.
    pred = None  # (cat, h, rows)
    for cat, h in builders:
        rows = run_entry_rows(xorq_bin, cat, h, limit=2000)
        if rows and _prediction_columns(rows[0]):
            pred = (cat, h, rows)
            break
    assert pred, f"no fitted-pipeline entry runs to predictions\nclaude said: {run.said}"

    # round-trip determinism: re-running reproduces it exactly (the same fitted model came back)
    cat, h, rows = pred
    assert canon(rows) == canon(run_entry_rows(xorq_bin, cat, h, limit=2000)), (
        f"predictions not deterministic across runs\nclaude said: {run.said}"
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
