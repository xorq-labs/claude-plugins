"""LLM end-to-end tests for the `composer` skill (xorq/skills/composer/SKILL.md).

Mirrors test_ingest_skill_llm.py: a real headless ``claude -p`` session with the xorq
plugin + CLAUDE.md injected, driven by short user-voice prompts (tests/prompts.py).

Setup is deterministic — ``seed_catalog_sources`` stands up a repo-local catalog with the
source files ALREADY added (so we are NOT retesting ingest). The model's job is to COMPOSE
a new derived entry on top. Assertions run that entry and check VALUES against ground truth
computed from the CSVs at runtime (no hardcoded drift), matched flexibly so the model's
column-naming choices don't matter.

Two prompt classes, one intent ("derive a new entry from the catalog"):
  - single-source shaping  -> `catalog compose`                       -> a `composed` entry
  - multi-source join      -> a build script (compose is single-input) -> an `expr` entry
``derived_entries`` accepts either kind, so both live here together.

Opt-in only: ``pytest -m llm``. Skips cleanly without claude / auth / fixture data.
"""

import pandas as pd
import pytest

from conftest import (
    DATA,
    assert_argmax,
    assert_derived,
    assert_grouped,
    seed_catalog_sources,
)
from prompts import Compose

pytestmark = pytest.mark.llm


# --- single-source shaping -> `catalog compose` -> composed ---


def test_llm_compose_per_tier(xorq_bin, claude_project, run_claude):
    """customers -> count per tier."""
    seed_catalog_sources(xorq_bin, claude_project, ["customers"])
    run = run_claude(Compose.PER_TIER)
    expected = pd.read_csv(DATA / "customers.csv").tier.value_counts().to_dict()
    assert_derived(xorq_bin, run, lambda rows: assert_grouped(rows, expected))


def test_llm_compose_top_category(xorq_bin, claude_project, run_claude):
    """transactions -> the category with the most revenue (argmax)."""
    seed_catalog_sources(xorq_bin, claude_project, ["transactions"])
    run = run_claude(Compose.TOP_CATEGORY)
    top = pd.read_csv(DATA / "transactions.csv").groupby("category").amount.sum().idxmax()  # 'groceries'
    assert_derived(xorq_bin, run, lambda rows: assert_argmax(rows, top))


# --- multi-source join -> build script (compose is single-input) -> expr ---


def test_llm_compose_spend_by_tier(xorq_bin, claude_project, run_claude):
    """transactions x customers (customer_id) -> total spend per tier."""
    seed_catalog_sources(xorq_bin, claude_project, ["transactions", "customers"])
    run = run_claude(Compose.SPEND_BY_TIER)
    txn = pd.read_csv(DATA / "transactions.csv")
    cust = pd.read_csv(DATA / "customers.csv")
    m = txn.merge(cust[["customer_id", "tier"]], on="customer_id")
    expected = m.groupby("tier").amount.sum().round(2).to_dict()
    assert_derived(xorq_bin, run, lambda rows: assert_grouped(rows, expected))


def test_llm_compose_free_fave(xorq_bin, claude_project, run_claude):
    """transactions x customers, free tier -> top spending category (argmax)."""
    seed_catalog_sources(xorq_bin, claude_project, ["transactions", "customers"])
    run = run_claude(Compose.FREE_FAVE)
    txn = pd.read_csv(DATA / "transactions.csv")
    cust = pd.read_csv(DATA / "customers.csv")
    free = txn.merge(cust[["customer_id", "tier"]], on="customer_id").query("tier == 'free'")
    top = free.groupby("category").amount.sum().idxmax()  # 'software' (also top by count)
    assert_derived(xorq_bin, run, lambda rows: assert_argmax(rows, top))
