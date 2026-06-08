"""Short, casual, user-voice prompts for the LLM e2e tests.

These are deliberately terse — the way a real user would ask — and name no paths,
aliases, or commands. That's the point: the test exercises whether the xorq skill
(+ the injected CLAUDE.md ambient context) is good enough to act on a casual request,
not whether the model can follow a spelled-out recipe.

`StrEnum` members *are* their string value, so a member can be passed straight to the
CLI runner: `run_claude(IngestData.RAW)`.

`IngestData` drives the `ingest` skill's LLM e2e (build path). Catalog acquire/copy
(clone / pull / replay) is xorq's own machinery, not a skill — out of scope for these
agent-success tests (we don't retest xorq). The rest are scaffolding so adding the other
skills (composer, ml, …) later is a one-liner.
"""

from enum import StrEnum


class IngestData(StrEnum):
    """xorq:ingest — originate Source entries from raw data (build)."""

    RAW = "Can you load my raw data into my raw catalog?"
    SQLITE = "Ingest the customers table from my sqlite db into a catalog."
    DUCKDB = "Get the customers table out of my duckdb into a catalog."
    POSTGRES = "Can you ingest my customers data from postgres into my postgres catalog?"


class ExploreCatalog(StrEnum):
    """xorq:catalog-explore — discover and inspect catalogued entries, read-only.

    The skill mutates nothing, so the e2e judges the model's ANSWER (does it name the right
    entries / columns?) and separately asserts the seeded catalog is left untouched. Prompts
    name no aliases or commands — with one entry seeded, "the data in my catalog" is unambiguous.
    """

    LIST = "What's in my catalog?"
    SCHEMA = "What columns does the data in my catalog have?"


class Compose(StrEnum):
    """xorq:composer — compose a new derived entry from catalogued expressions.

    Single-source prompts exercise `catalog compose` (-> a `composed` entry); the join
    prompts force a build script since compose is single-input (-> an `expr` entry). Same
    intent either way: "derive a new entry from what's in the catalog."
    """

    # single-source shaping -> `catalog compose` -> composed
    BY_REGION = "From what's in my catalog, break down total revenue and order counts by region and save it as a new entry."
    PER_TIER = "How many customers are in each tier? Save the breakdown to the catalog."
    TOP_CATEGORY = "Which transaction category pulls in the most revenue? Add the answer to my catalog."

    # multi-source join -> build script -> expr
    SPEND_BY_TIER = "What's the total transaction spend for each customer tier? Save it as a new entry."
    FREE_FAVE = "What do my free-tier customers spend the most on? Add the answer to the catalog."


class Build(StrEnum):
    """xorq:builder — fit pipelines / semantic models (not yet wired)."""

    FIT = "Fit a simple model on my data and save it to the catalog."
