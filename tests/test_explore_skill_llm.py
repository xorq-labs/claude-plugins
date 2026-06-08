"""LLM end-to-end tests for the `catalog-explore` skill (xorq/skills/catalog-explore/SKILL.md).

Mirrors the other ``*_skill_llm`` tests: a real headless ``claude -p`` session with the xorq
plugin loaded via ``--plugin-dir`` and xorq/CLAUDE.md injected, driven by short, user-voice
prompts (tests/prompts.py).

catalog-explore is READ-ONLY — it produces no artifact — so the contract is twofold:
  1. the model's ANSWER names the right entries / columns (it could only know them by actually
     inspecting the catalog), and
  2. the seeded catalog is left UNCHANGED (the skill added / removed nothing).

Setup reuses ``seed_catalog_sources`` — the same deterministic repo-local catalog the composer
e2e builds on — so resolution finds it as the single repo-local candidate and the model
inspects it rather than creating a fresh empty one.

Opt-in only: ``pytest -m llm``. Skips cleanly when ``claude`` isn't installed, when there's no
auth (ANTHROPIC_API_KEY / ~/.claude), or when the fixture data is absent.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from conftest import (
    DATA,
    assert_answer_mentions,
    assert_read_only,
    catalog_snapshot,
    seed_catalog_sources,
)
from prompts import ExploreCatalog

pytestmark = pytest.mark.llm

SEEDED = ["customers", "products", "transactions"]


def test_llm_explore_list(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """"What's in my catalog?" -> the answer names every seeded entry; nothing is mutated."""
    cat = seed_catalog_sources(xorq_bin, claude_project, SEEDED)
    before = catalog_snapshot(xorq_bin, cat)
    run = run_claude(ExploreCatalog.LIST)
    assert_answer_mentions(run, *SEEDED)        # all three aliases reported
    assert_read_only(xorq_bin, cat, before)     # explore added/removed nothing


def test_llm_explore_schema(xorq_bin: str, claude_project: Path, run_claude: Callable) -> None:
    """One entry seeded -> "what columns?" makes the model inspect its schema and report it."""
    cat = seed_catalog_sources(xorq_bin, claude_project, ["customers"])
    before = catalog_snapshot(xorq_bin, cat)
    run = run_claude(ExploreCatalog.SCHEMA)
    cols = list(pd.read_csv(DATA / "customers.csv").columns)  # derived from the fixture (no drift)
    assert_answer_mentions(run, *cols, min_hits=len(cols) - 1)  # tolerate one summarized-away column
    assert_read_only(xorq_bin, cat, before)
