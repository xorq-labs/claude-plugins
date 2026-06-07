"""Short, casual, user-voice prompts for the LLM e2e tests.

These are deliberately terse — the way a real user would ask — and name no paths,
aliases, or commands. That's the point: the test exercises whether the xorq skill
(+ the injected CLAUDE.md ambient context) is good enough to act on a casual request,
not whether the model can follow a spelled-out recipe.

`StrEnum` members *are* their string value, so a member can be passed straight to the
CLI runner: `run_claude(IngestData.RAW)`.

`IngestData` drives the `ingest` skill's LLM e2e (build path). The `init` skill (no-build
acquire: clone / submodule / pull / replay / get+add) is covered by deterministic contract
tests (test_init_contract.py); it has no LLM e2e yet — acquiring needs a published catalog
to clone from. The rest are scaffolding so adding the other skills later is a one-liner.
"""

from enum import StrEnum


class IngestData(StrEnum):
    """xorq:ingest — originate Source entries from raw data (build)."""

    RAW = "Can you load my raw data into my raw catalog?"
    SQLITE = "Ingest the customers table from my sqlite db into a catalog."
    DUCKDB = "Get the customers table out of my duckdb into a catalog."
    POSTGRES = "Can you ingest my customers data from postgres into my postgres catalog?"


class ExploreCatalog(StrEnum):
    """xorq:catalog-explore — discover and inspect entries (not yet wired)."""

    LIST = "What's in my catalog?"


class Compose(StrEnum):
    """xorq:composer — combine, transform, and run entries (not yet wired)."""

    RUN = "Run the customers entry and show me a few rows."


class Build(StrEnum):
    """xorq:builder — fit pipelines / semantic models (not yet wired)."""

    FIT = "Fit a simple model on my data and save it to the catalog."
