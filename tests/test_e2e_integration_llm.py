"""End-to-end *integration* LLM tests — one casual request, a whole multi-skill flow.

The other ``*_skill_llm`` tests each exercise a single skill on a pre-seeded catalog. These
go wider: each prompt (tests/prompts.py ``Integration``) is one user-voice ask that the model
must decompose across skills, end to end, with no catalog seeded for it. They mirror the three
requests of a single working session, but run as independent tests so a failure in one is
isolated and debuggable.

Three scenarios, three deliverables — each asserted on the deterministic artifacts the model
leaves behind (never on the model's prose):

  1. semantic-bts  — clone a PUBLIC remote catalog locally (it ships a `flights` source + a
     `semantic-flights` BSL model), then COMPOSE two new entries on top: one answering a
     flights question (Ohio inbound on Mondays / California outbound on Tuesdays) and one off
     the semantic model giving delay-by-departure-time-block for departures AND arrivals. The
     flights-question entry is checked structurally (stored SQL + schema; build is lazy + offline).
     The time-block entry is checked DETERMINISTICALLY but as a SUBSET: we recompute the canonical
     answer from the same semantic model at whatever block grouping it used, pinned to one immutable
     BTS month, and assert its two delay columns equal ours per block — ignoring any extra columns it
     added (so it's not brittle). This part fetches BTS data, so it's skipped on any fetch failure.
  2. penguins      — INGEST a local CSV into a new `penguins` catalog, then COMPOSE the
     heaviest/tiniest answer. Runs offline, so we assert VALUES (the extreme body masses).
  3. iris          — INGEST a local CSV into a new `iris` catalog, then fit TWO from-instance
     pipelines (classify species; regress petal width). We recover each FittedPipeline from the
     catalog and SCORE it: AUC for the classifier, MSE for the regressor.

The penguins/iris CSVs are GENERATED at runtime into the tmp project (iris from sklearn, offline;
penguins from the pinned xorq example, which needs the examples bucket) — nothing is committed. The
network the suite needs: the penguins example fetch, a GitHub clone of the BTS catalog, and (for the
BTS deterministic check) one month of BTS flight data — each gated/skipped when unreachable.

Opt-in only: ``pytest -m llm``. Skips cleanly without claude / auth / fixture data.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import pytest

from conftest import (
    all_catalogs,
    bts_candidate_catalogs,
    bts_expected_timeblock_rows,
    bts_run_entry_rows,
    builder_metric,
    catalog_aliases,
    catalog_entries,
    derived_in,
    entry_schema,
    entry_show,
    entry_sql,
    file_schema,
    github_reachable,
    named_catalog,
    run_entry_rows,
    seed_iris,
    seed_penguins,
)
from prompts import Integration

pytestmark = pytest.mark.llm


# --- 1) semantic-bts: clone a remote catalog locally, compose two new entries -----------------


@pytest.mark.skipif(not github_reachable(), reason="github.com:443 unreachable (BTS clone needs it)")
def test_llm_integration_semantic_bts(
    xorq_bin: str, claude_project: Path, run_claude: Callable
) -> None:
    """Clone the public BTS catalog into a writable local copy, compose two new entries on it.

    Flights-question entry — checked structurally offline (build is lazy; BTS data isn't cached):
    its stored SQL references BOTH Ohio and California with a day-of-week filter (it could only
    answer the question by encoding exactly that). Found by signature across the local catalog(s)
    the model produced (it may compose into a fresh repo-local catalog referencing the cloned source,
    so the original `flights` / `semantic-flights` aliases need not be present).

    Time-block entry — checked DETERMINISTICALLY but as a SUBSET: we recompute the canonical answer
    from the same semantic model (avg departure/arrival delay) at WHATEVER block grouping the model
    chose — read off its schema, 1-D or 2-D — pinned to one immutable BTS month, then assert the
    model's two delay columns equal ours per block. Only those two columns are compared, so extra
    columns the model adds (n_flights, pct-of-block, …) don't make it brittle. Both sides run via
    `xorq run <build> -p` (not `catalog run -p`, which can't re-parameterize the UDXF in 0.3.29);
    skipped on any BTS fetch failure.
    """
    run = run_claude(Integration.SEMANTIC_BTS, timeout=600)

    cats = bts_candidate_catalogs(run)
    assert cats, f"no local catalog produced for the BTS work\nclaude said: {run.said}"

    ohio = cali = dow = False
    timeblock_entry = None  # (cat, hash) of the avg-delay-by-time-block entry
    for cat in cats:
        for h in derived_in(xorq_bin, cat):
            sql = entry_sql(xorq_bin, cat, h)
            # flights question: Ohio inbound / California outbound, by day of week. Accept state
            # codes ('OH'/'CA') or full names; the two halves may live in one entry or two.
            ohio = ohio or "'oh'" in sql or "ohio" in sql
            cali = cali or "'ca'" in sql or "california" in sql
            dow = dow or "dayofweek" in sql or "day_of_week" in sql
            # time-block delays: a time-of-day block dimension + an avg departure-delay AND an avg
            # arrival-delay measure (ignore pct-of-block / minutes / 15-min-flag variants).
            cols = {c.lower() for c in entry_schema(xorq_bin, cat, h)}
            has_block = any("time_blk" in c or "timeblk" in c or "time_block" in c for c in cols)
            delay = lambda side: any(  # noqa: E731 — a mean dep/arr delay column, not a variant
                side in c and "delay" in c and not any(x in c for x in ("pct", "block", "minute", "group", "del15"))
                for c in cols
            )
            if has_block and delay("dep") and delay("arr"):
                timeblock_entry = timeblock_entry or (cat, h)

    assert ohio and cali and dow, (
        f"flights-question entry not found (ohio={ohio}, california={cali}, day_of_week={dow})\n"
        f"claude said: {run.said}"
    )
    assert timeblock_entry is not None, (
        "no entry carries a time-of-day block dimension plus average departure- and arrival-delay "
        f"measures\nclaude said: {run.said}"
    )

    # deterministic SUBSET value check: the model's two delay columns must equal the canonical answer
    # we recompute from the semantic model at the SAME block grouping it used (1-D or 2-D).
    tb_cat, tb_hash = timeblock_entry
    block_dims = [c for c in entry_schema(xorq_bin, tb_cat, tb_hash)
                  if any(x in c.lower() for x in ("time_blk", "timeblk", "time_block"))]
    work = claude_project.parent / "_bts_expected"
    cache = claude_project.parent / "_bts_cache"
    try:
        expected = bts_expected_timeblock_rows(xorq_bin, work, cache, dimensions=tuple(block_dims))
        actual = bts_run_entry_rows(xorq_bin, tb_cat, tb_hash, cache)
    except (RuntimeError, subprocess.TimeoutExpired) as e:
        pytest.skip(f"BTS data unavailable for the deterministic check: {e}")
    _assert_timeblock_subset(actual, expected, block_dims, run=run)


# --- 2) penguins: ingest a local CSV into a `penguins` catalog, compose heaviest/tiniest ------


def test_llm_integration_penguins(
    xorq_bin: str, claude_project: Path, run_claude: Callable
) -> None:
    """Ingest penguins.csv into a new `penguins` catalog, then identify the heaviest/tiniest.

    Offline, so we assert VALUES — and crucially the ATTRIBUTION, not mere existence. "Heaviest/
    tiniest" reads as body mass: the heaviest penguin is a Gentoo at 6300 g, the tiniest a Chinstrap
    at 2700 g (both derived from the fixture). A check that only asks "do 6300 and 2700 both appear?"
    would pass even if the model swapped them, so instead we bind each answer to its label:
      - if the model saved separately-labeled entries, the *heaviest*-labelled entry must return the
        dataset MAX (Gentoo, 6300 g) and the *tiniest*-labelled entry the MIN (Chinstrap, 2700 g) —
        a swap (heaviest entry returning the lightest bird) fails; or
      - if it saved one combined entry, that entry must contain BOTH extreme rows (the rows ARE the
        max/min, so attribution is structural).
    """
    penguins_csv = seed_penguins(claude_project)  # generated from xorq.examples into the tmp project
    run = run_claude(Integration.PENGUINS)

    cat = named_catalog(run, "penguin") or _catalog_with_column(xorq_bin, run, "body_mass_g")
    assert cat is not None, f"no `penguins` catalog created\nclaude said: {run.said}"

    # the raw data became a source entry with the penguins schema
    _assert_source_has_columns(xorq_bin, cat, penguins_csv)

    p = pd.read_csv(penguins_csv)
    hi = p.loc[p.body_mass_g.idxmax()]  # the heaviest penguin: Gentoo, 6300 g
    lo = p.loc[p.body_mass_g.idxmin()]  # the tiniest penguin:  Chinstrap, 2700 g

    # Run every compact answer entry (extract + `xorq run`, robust to materialized sources) and tag
    # each row with the claim it carries — from a "heaviest"/"tiniest" label cell, or from the entry
    # alias. Then bind claim -> value: a "heaviest" answer must be the dataset MAX (Gentoo, 6300 g),
    # a "tiniest" answer the MIN (Chinstrap, 2700 g) — so a swap fails.
    claimed: list[tuple[str, dict]] = []  # (claim, row), claim in {"heavy","tiny"}
    answer_entries: list[list] = []       # rows of each compact entry, for the no-label fallback
    for alias in catalog_aliases(xorq_bin, cat):
        rows = run_entry_rows(xorq_bin, cat, alias)
        if not rows or len(rows) > 25:  # skip the source (344 rows) / anything not an answer
            continue
        answer_entries.append(rows)
        alias_claim = _penguin_claim(alias)
        for r in rows:
            row_claim = next((c for v in r.values() if isinstance(v, str) and (c := _penguin_claim(v))), None)
            if claim := (row_claim or alias_claim):
                claimed.append((claim, r))

    if claimed:
        assert any(c == "heavy" and _is_penguin(r, hi.body_mass_g, hi.species) for c, r in claimed), (
            f"no 'heaviest' answer returns the heaviest penguin ({hi.species}, {hi.body_mass_g:g} g)"
            f"\nclaude said: {run.said}"
        )
        assert any(c == "tiny" and _is_penguin(r, lo.body_mass_g, lo.species) for c, r in claimed), (
            f"no 'tiniest' answer returns the tiniest penguin ({lo.species}, {lo.body_mass_g:g} g)"
            f"\nclaude said: {run.said}"
        )
        # swap guard: a "heaviest" answer must not be the tiniest bird, and vice versa
        assert not any(c == "heavy" and _is_penguin(r, lo.body_mass_g, lo.species) for c, r in claimed) \
            and not any(c == "tiny" and _is_penguin(r, hi.body_mass_g, hi.species) for c, r in claimed), (
            f"heaviest/tiniest are swapped\nclaude said: {run.said}"
        )
    else:  # no label/alias distinguishes them -> one entry must hold BOTH extreme rows (swap-proof)
        assert any(
            _is_in_rows(rows, hi.body_mass_g, hi.species) and _is_in_rows(rows, lo.body_mass_g, lo.species)
            for rows in answer_entries
        ), (
            f"no entry identifies both the heaviest ({hi.species}, {hi.body_mass_g:g} g) and the "
            f"tiniest ({lo.species}, {lo.body_mass_g:g} g) penguin\nclaude said: {run.said}"
        )


# --- 3) iris: ingest into an `iris` catalog, fit two from-instance pipelines, score them ------

# Iris is trivially separable; even a weak model clears these. A degenerate one (predict-the-mean
# regressor ~ 0.58 MSE; a coin-flip classifier ~ 0.5 AUC) fails them.
MIN_CLASSIFIER_AUC = 0.90
MAX_REGRESSOR_MSE = 0.30


def test_llm_integration_iris(
    xorq_bin: str, claude_project: Path, run_claude: Callable
) -> None:
    """Ingest iris.csv into a new `iris` catalog, fit a species classifier and a petal-width
    regressor as from-instance pipelines, and assert BOTH round-trip and score well.

    We recover each FittedPipeline from its catalogued entry and score it on the iris data: a
    classification scorer (roc_auc_ovr) succeeds only on the classifier, a regression scorer
    (neg_mean_squared_error) only on the regressor — so the two models identify themselves by
    which metric they admit. Asserts the `iris` catalog exists with the source + 2 entries that are
    `expr_builder` kind AND report a `fitted_pipeline` builder (so they're genuine from-instance
    FittedPipelines, not just any builder), the classifier's AUC clears MIN_CLASSIFIER_AUC, and the
    regressor's MSE is under MAX_REGRESSOR_MSE.
    """
    iris_csv = seed_iris(claude_project)  # generated from sklearn into the tmp project
    run = run_claude(Integration.IRIS, timeout=900)

    cat = named_catalog(run, "iris") or _catalog_with_column(xorq_bin, run, "petal_width")
    assert cat is not None, f"no `iris` catalog created\nclaude said: {run.said}"

    _assert_source_has_columns(xorq_bin, cat, iris_csv)

    # both models must be expr_builder entries that round-trip as FittedPipelines (what `from_instance`
    # + `.predict()` produces) — `show` reports a `fitted_pipeline` builder block for each.
    builders = [h for h, k in catalog_entries(xorq_bin, cat) if k == "expr_builder"]
    models = [h for h in builders if "fitted_pipeline" in entry_show(xorq_bin, cat, h)]
    assert len(models) >= 2, (
        f"expected 2 fitted-pipeline expr_builder entries, got {len(models)} fitted of "
        f"{len(builders)} expr_builder: {builders}\nclaude said: {run.said}"
    )

    aucs, mses = [], []
    for h in models:
        auc = builder_metric(xorq_bin, cat, h, iris_csv, "roc_auc_ovr")
        if auc is not None:
            aucs.append(auc)
        neg_mse = builder_metric(xorq_bin, cat, h, iris_csv, "neg_mean_squared_error")
        if neg_mse is not None:
            mses.append(-neg_mse)

    assert any(a >= MIN_CLASSIFIER_AUC for a in aucs), (
        f"no fitted model scores as a species classifier with AUC >= {MIN_CLASSIFIER_AUC} "
        f"(AUCs seen: {aucs})\nclaude said: {run.said}"
    )
    assert any(m <= MAX_REGRESSOR_MSE for m in mses), (
        f"no fitted model scores as a petal-width regressor with MSE <= {MAX_REGRESSOR_MSE} "
        f"(MSEs seen: {mses})\nclaude said: {run.said}"
    )


# --- small shared helpers (kept here, not in conftest, as they're integration-specific) -------


def _catalog_with_column(xorq_bin: str, run, column: str) -> Path | None:
    """Fallback catalog finder: the catalog holding an entry whose schema has ``column``
    (identifies the penguins/iris catalog by its data when the model named the dir oddly)."""
    for cat in all_catalogs(run):
        for h, _ in catalog_entries(xorq_bin, cat):
            if column in entry_schema(xorq_bin, cat, h):
                return cat
    return None


def _assert_source_has_columns(xorq_bin: str, cat: Path, csv: Path) -> None:
    """Assert some entry in ``cat`` has every column of ``csv`` (the ingested source schema)."""
    want = set(file_schema(csv))
    got = [set(entry_schema(xorq_bin, cat, h)) for h, _ in catalog_entries(xorq_bin, cat)]
    assert any(want <= cols for cols in got), (
        f"no entry carries the full {csv.name} schema {sorted(want)}; entry columns: {got}"
    )


_HEAVY_KW = ("heav", "big", "large", "max", "most", "top", "largest")
_TINY_KW = ("tini", "small", "min", "light", "least", "low", "lightest", "smallest")


def _penguin_claim(text: str) -> str | None:
    """Classify a label/alias as a 'heavy' or 'tiny' claim (or None)."""
    t = str(text).lower()
    if any(k in t for k in _HEAVY_KW):
        return "heavy"
    if any(k in t for k in _TINY_KW):
        return "tiny"
    return None


def _is_penguin(row: dict, mass: float, species: str) -> bool:
    """True if a row's body mass (g) equals ``mass`` and — if it has a species column — ``species``.

    Binds the numeric answer to the actual bird: 6300 g / 2700 g are the dataset extremes, so a row
    tagged 'heaviest' that carries 6300 g (Gentoo) is the correct heaviest, not a coincidence.
    """
    masses = [v for k, v in row.items()
              if "mass" in k.lower() and isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not any(abs(m - mass) <= 1 for m in masses):
        return False
    species_vals = [str(v) for k, v in row.items() if "species" in k.lower() and isinstance(v, str)]
    return not species_vals or any(s.lower() == str(species).lower() for s in species_vals)


def _is_in_rows(rows: list, mass: float, species: str) -> bool:
    """True if any row matches the given penguin (mass + species)."""
    return any(_is_penguin(r, mass, species) for r in rows)


def _assert_timeblock_subset(actual_rows: list, expected_rows: list, block_dims: list, *, run) -> None:
    """Deterministic SUBSET check: the model's avg dep/arr delay (keyed by its block grouping) must
    equal the values we recompute from the semantic model. Only those two columns are compared, keyed
    by ``block_dims`` — extra columns the model added (n_flights, pct-of-block, …) are ignored, so it
    isn't brittle. Nulls (sparse grid cells) compare equal; floats within a small tolerance.
    """
    def delay_col(r0: dict, side_kw: str) -> str | None:
        return next((c for c in r0 if side_kw in c.lower() and "delay" in c.lower()
                     and not any(x in c.lower() for x in ("pct", "block", "minute", "group", "del15"))), None)

    def index(rows: list, side: str) -> tuple:
        r0 = rows[0]
        keys = [d for d in block_dims if d in r0]
        dep, arr = delay_col(r0, "dep"), delay_col(r0, "arr")
        assert keys and dep and arr, (
            f"{side} missing block key(s) {block_dims} / dep-delay / arr-delay; cols={list(r0)}\n"
            f"claude said: {run.said}"
        )
        return {tuple(str(r[k]) for k in keys): (r[dep], r[arr]) for r in rows}, keys

    actual, _ = index(actual_rows, "model expr")
    expected, keys = index(expected_rows, "canonical")

    def close(x: object, y: object) -> bool:
        if x is None or y is None:
            return x is None and y is None          # both null (sparse cell)
        return abs(float(x) - float(y)) <= 0.01

    missing = [k for k in expected if k not in actual]
    mismatch = [(k, expected[k], actual[k]) for k in expected
                if k in actual and not (close(expected[k][0], actual[k][0])
                                        and close(expected[k][1], actual[k][1]))]
    assert not missing and not mismatch, (
        f"time-block avg delays don't match the canonical answer from the semantic model "
        f"(keys {keys})\n  missing keys: {missing[:5]}\n  mismatched: {mismatch[:5]}\n"
        f"claude said: {run.said}"
    )


