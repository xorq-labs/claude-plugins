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

from __future__ import annotations

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
    PER_TIER = "How many customers are in each tier? Save the breakdown to the catalog."
    TOP_CATEGORY = "Which transaction category pulls in the most revenue? Add the answer to my catalog."

    # multi-source join -> build script -> expr
    SPEND_BY_TIER = "What's the total transaction spend for each customer tier? Save it as a new entry."
    FREE_FAVE = "What do my free-tier customers spend the most on? Add the answer to the catalog."


class Builder(StrEnum):
    """xorq:builder — round-trip ExprBuilders: semantic models, fitted pipelines, custom builders.

    Plain-English, enough-info prompts (no aliases/commands). Each exercises the build → catalog →
    recover (`.ls.builder`) → re-use loop on a different builder instance.
    """

    # semantic model: spend by category from transactions
    CATEGORY_SPEND = (
        "Build a semantic model from my transactions that gives me total spend per category, "
        "and save it to the catalog."
    )
    # semantic model + join_one: tier lives on customers, relate each transaction to its one customer
    SPEND_BY_TIER = (
        "Now I want spend broken down by customer tier. The transactions don't carry the tier — "
        "it's on the customers — so relate each transaction to its one customer and build that as "
        "a semantic model saved to the catalog."
    )
    # fitted pipeline round-trip: train on dev events, catalog, recover, infer on prod events
    ML_PURCHASE = (
        "Train a model on my dev events to predict whether an event is a purchase, save the "
        "fitted model to the catalog, then load it back from the catalog and use it to score my "
        "prod events — saving the scored prod events to the catalog too."
    )
    # custom builder round-trip: use the provided builder, build first-3, recover, do last-3
    TEXT_SLICE = (
        "I've added a small builder in slice_builder.py that can take the first or last N "
        "characters of a text column (importing it registers it). Use it to build and save an "
        "entry with the first 3 characters of the product id; then load that entry back from the "
        "catalog, recover the builder from it, switch it to the last 3 characters, and save that "
        "as a new entry."
    )


class Integration(StrEnum):
    """Multi-skill end-to-end scenarios — each prompt exercises a whole flow, not one skill.

    These are the heaviest e2e cases: a single casual request that the model must decompose
    across skills (acquire/clone -> compose, or ingest -> ml). Still user-voice and naming no
    aliases/commands, but each names enough to be achievable headless — which catalog to make,
    or that the remote catalog is read-only so a local copy is needed.

    SEMANTIC_BTS leans on a public catalog (the `xorq-catalog-bts` submodule of semantic-bts),
    which ships a `flights` source + a `semantic-flights` BSL model. Its flight data is fetched
    on demand from BTS and isn't cached here, so the deliverable is *composed + catalogued*
    expressions (build is lazy and offline) — the prompt says not to execute them.
    """

    # clone-the-remote -> compose: one expr answering the flights question, one off the
    # semantic model showing delay-by-time-block for departures AND arrivals.
    SEMANTIC_BTS = (
        "Go grab the xorq flights catalog from https://github.com/xorq-labs/semantic-bts — it "
        "ships a xorq catalog (the xorq-catalog-bts submodule) with a `flights` source and a "
        "`semantic-flights` semantic model. We can't and shouldn't sync back to that remote — work "
        "entirely in a local copy you can write to, and don't push anything. The underlying flight "
        "data is large and isn't cached in here, "
        "so don't run the queries — just compose the expressions and save them to your local "
        "catalog. First: how many flights go into Ohio on Mondays and out of California on Tuesdays? "
        "Then, using the semantic model, build one that's keyed by the departure time-of-day block — "
        "exactly one row per block — with two measures on each row: the average departure delay and "
        "the average arrival delay (ordered by block). I just want those two delay numbers per "
        "departure block, not a departure-by-arrival-block breakdown — so I can see whether delays "
        "build up later in the day."
    )

    # ingest -> compose: stand up a named catalog from a local file, then derive the answer.
    PENGUINS = (
        "I've got penguin data in my project at data/penguins.csv. Add it to a new catalog called "
        "penguins, then compose an expression that tells me which is the heaviest penguin and which "
        "is the tiniest, and save that to the catalog too."
    )

    # ml x2 (from_instance): two fitted pipelines on one catalog — a classifier and a regressor.
    IRIS = (
        "Make another catalog, call it iris, from my data at data/iris.csv. Then build two ML models "
        "on it and save both to the catalog: one that predicts which type of iris it is (the species), "
        "and one that predicts how wide the petals are (the petal width). Use a from-instance sklearn "
        "pipeline for each."
    )
