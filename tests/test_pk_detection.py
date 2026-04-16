"""Tests for PK detection using canned 1000-row, 12-column datasets.

Five synthetic datasets exercise different PK profiles:
1. clear_single_pk   — one column with unique IDs
2. composite_pk      — two columns together form a unique key
3. no_pk             — every column has significant duplicates
4. near_miss_pk      — one column is 99.8% unique (below default 99.9% threshold)
5. multiple_candidates — two single-column PKs exist; should return the first
"""

from __future__ import annotations

import sys
from pathlib import Path

import polars as pl
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from pk_detection import detect_pk

N = 1000
SEED = 7


# ---------------------------------------------------------------------------
# Fixture helpers — build 12-column DataFrames
# ---------------------------------------------------------------------------


def _filler_cols(n: int, prefix: str = "col") -> dict[str, list]:
    """Generate 10 non-unique filler columns (str/int/float mix)."""
    import random

    rng = random.Random(SEED)
    cols: dict[str, list] = {}

    # 4 string columns with ~50 distinct values each
    for i in range(4):
        pool = [f"{prefix}_{i}_val_{v}" for v in range(50)]
        cols[f"{prefix}_str_{i}"] = [rng.choice(pool) for _ in range(n)]

    # 3 int columns with ~20 distinct values each
    for i in range(3):
        cols[f"{prefix}_int_{i}"] = [rng.randint(0, 19) for _ in range(n)]

    # 3 float columns (continuous but binned to ~100 distinct)
    for i in range(3):
        cols[f"{prefix}_flt_{i}"] = [round(rng.uniform(0, 100), 1) for _ in range(n)]

    return cols


# ---------------------------------------------------------------------------
# Dataset 1: clear single-column PK
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_single_pk() -> pl.DataFrame:
    """Row ID column is perfectly unique; 11 filler columns."""
    data = {"row_id": list(range(N))}
    data.update(_filler_cols(N))
    # trim to exactly 12 columns (row_id + 11 fillers, we have row_id + 10 = 11, need one more)
    data["extra"] = [i % 7 for i in range(N)]
    df = pl.DataFrame(data)
    assert df.shape == (N, 12)
    return df


def test_clear_single_pk(clear_single_pk):
    result = detect_pk(clear_single_pk)
    assert result == ["row_id"]


# ---------------------------------------------------------------------------
# Dataset 2: composite PK (two columns together unique, neither alone)
# ---------------------------------------------------------------------------


@pytest.fixture
def composite_pk() -> pl.DataFrame:
    """group (50 values) x seq (20 values) = 1000 unique pairs."""
    groups = [f"g{i // 20}" for i in range(N)]
    seqs = [i % 20 for i in range(N)]
    data = {"group": groups, "seq": seqs}
    fillers = _filler_cols(N, prefix="c")
    # take exactly 10 filler cols to reach 12 total
    for k, v in list(fillers.items())[:10]:
        data[k] = v
    df = pl.DataFrame(data)
    assert df.shape == (N, 12)
    return df


def test_composite_pk(composite_pk):
    result = detect_pk(composite_pk)
    assert result is not None
    assert set(result) == {"group", "seq"}
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Dataset 3: no PK — all columns have heavy duplication
# ---------------------------------------------------------------------------


@pytest.fixture
def no_pk() -> pl.DataFrame:
    """All 12 columns have very low cardinality — no combo of up to 4 is unique.

    5 distinct values per column means max 5^4 = 625 combos for any 4 columns,
    well below 1000 rows.
    """
    import random

    rng = random.Random(SEED + 1)
    data: dict[str, list] = {}
    for i in range(6):
        pool = [f"cat_{i}_{v}" for v in range(5)]
        data[f"cat_{i}"] = [rng.choice(pool) for _ in range(N)]
    for i in range(6):
        data[f"num_{i}"] = [rng.randint(0, 4) for _ in range(N)]
    df = pl.DataFrame(data)
    assert df.shape == (N, 12)
    return df


def test_no_pk(no_pk):
    result = detect_pk(no_pk)
    assert result is None


# ---------------------------------------------------------------------------
# Dataset 4: near-miss PK — 99.8% unique (below 99.9% threshold)
# ---------------------------------------------------------------------------


@pytest.fixture
def near_miss_pk() -> pl.DataFrame:
    """almost_id has 998 unique values out of 1000 (99.8% < 99.9% threshold)."""
    ids = list(range(N))
    # introduce 2 duplicates: rows 998 and 999 copy rows 0 and 1
    ids[998] = 0
    ids[999] = 1
    data = {"almost_id": ids}
    data.update(_filler_cols(N, prefix="nm"))
    data["pad"] = [i % 3 for i in range(N)]
    df = pl.DataFrame(data)
    assert df.shape == (N, 12)
    return df


def test_near_miss_pk_rejected_at_default_threshold(near_miss_pk):
    """99.8% unique should NOT be detected as PK at default 0.999 threshold."""
    result = detect_pk(near_miss_pk)
    # almost_id alone shouldn't qualify
    assert result is None or result != ["almost_id"]


def test_near_miss_pk_accepted_at_lower_threshold(near_miss_pk):
    """99.8% unique should be detected as PK when threshold is lowered to 0.99."""
    result = detect_pk(near_miss_pk, threshold=0.99)
    assert result == ["almost_id"]


# ---------------------------------------------------------------------------
# Dataset 5: multiple candidate single-column PKs
# ---------------------------------------------------------------------------


@pytest.fixture
def multiple_candidates() -> pl.DataFrame:
    """Two columns are each perfectly unique; detect_pk should return the first."""
    import random

    rng = random.Random(SEED + 2)
    pk_a = list(range(N))
    pk_b = list(range(N, 2 * N))
    rng.shuffle(pk_b)
    data = {"pk_a": pk_a, "pk_b": pk_b}
    fillers = _filler_cols(N, prefix="mc")
    for k, v in list(fillers.items())[:10]:
        data[k] = v
    df = pl.DataFrame(data)
    assert df.shape == (N, 12)
    return df


def test_multiple_candidates_returns_first(multiple_candidates):
    """When multiple single-column PKs exist, the first column wins."""
    result = detect_pk(multiple_candidates)
    assert result == ["pk_a"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_dataframe():
    df = pl.DataFrame({"a": [], "b": []}).cast({"a": pl.Int64, "b": pl.Int64})
    assert detect_pk(df) is None


def test_single_row():
    df = pl.DataFrame({"a": [1], "b": [2]})
    result = detect_pk(df)
    assert result == ["a"]


# ---------------------------------------------------------------------------
# Parametric: run detect_pk on all 5 fixtures and verify shape invariants
# ---------------------------------------------------------------------------


@pytest.fixture(
    params=[
        "clear_single_pk",
        "composite_pk",
        "no_pk",
        "near_miss_pk",
        "multiple_candidates",
    ]
)
def any_dataset(request) -> pl.DataFrame:
    return request.getfixturevalue(request.param)


def test_all_datasets_are_1000x12(any_dataset):
    assert any_dataset.shape == (N, 12)


def test_detect_pk_returns_list_or_none(any_dataset):
    result = detect_pk(any_dataset)
    assert result is None or isinstance(result, list)


def test_detect_pk_result_columns_exist_in_dataframe(any_dataset):
    result = detect_pk(any_dataset)
    if result is not None:
        for col in result:
            assert col in any_dataset.columns
