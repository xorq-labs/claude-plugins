"""Tests for xorq/ibis PK detection using canned 1000-row, 12-column datasets.

Same five scenarios as test_pk_detection.py but data is loaded as ibis
memtables running on the xorq (DataFusion) backend.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))
from pk_detection_xorq import detect_pk

# xorq vendor ibis — must use this, not upstream ibis
import xorq.vendor.ibis as ibis

N = 1000
SEED = 7


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _filler_cols(n: int, prefix: str = "col") -> dict[str, list]:
    """Generate 10 non-unique filler columns (str/int/float mix)."""
    import random

    rng = random.Random(SEED)
    cols: dict[str, list] = {}

    for i in range(4):
        pool = [f"{prefix}_{i}_val_{v}" for v in range(50)]
        cols[f"{prefix}_str_{i}"] = [rng.choice(pool) for _ in range(n)]

    for i in range(3):
        cols[f"{prefix}_int_{i}"] = [rng.randint(0, 19) for _ in range(n)]

    for i in range(3):
        cols[f"{prefix}_flt_{i}"] = [round(rng.uniform(0, 100), 1) for _ in range(n)]

    return cols


# ---------------------------------------------------------------------------
# Dataset 1: clear single-column PK
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_single_pk():
    data = {"row_id": list(range(N))}
    data.update(_filler_cols(N))
    data["extra"] = [i % 7 for i in range(N)]
    t = ibis.memtable(data)
    assert t.count().execute() == N
    assert len(t.columns) == 12
    return t


def test_clear_single_pk(clear_single_pk):
    result = detect_pk(clear_single_pk)
    assert result == ["row_id"]


# ---------------------------------------------------------------------------
# Dataset 2: composite PK (two columns together unique, neither alone)
# ---------------------------------------------------------------------------


@pytest.fixture
def composite_pk():
    groups = [f"g{i // 20}" for i in range(N)]
    seqs = [i % 20 for i in range(N)]
    data = {"group": groups, "seq": seqs}
    fillers = _filler_cols(N, prefix="c")
    for k, v in list(fillers.items())[:10]:
        data[k] = v
    t = ibis.memtable(data)
    assert t.count().execute() == N
    assert len(t.columns) == 12
    return t


def test_composite_pk(composite_pk):
    result = detect_pk(composite_pk)
    assert result is not None
    assert set(result) == {"group", "seq"}
    assert len(result) == 2


# ---------------------------------------------------------------------------
# Dataset 3: no PK — all columns have very low cardinality
# ---------------------------------------------------------------------------


@pytest.fixture
def no_pk():
    """5 distinct values per column; max 5^4 = 625 combos < 1000 rows."""
    import random

    rng = random.Random(SEED + 1)
    data: dict[str, list] = {}
    for i in range(6):
        pool = [f"cat_{i}_{v}" for v in range(5)]
        data[f"cat_{i}"] = [rng.choice(pool) for _ in range(N)]
    for i in range(6):
        data[f"num_{i}"] = [rng.randint(0, 4) for _ in range(N)]
    t = ibis.memtable(data)
    assert t.count().execute() == N
    assert len(t.columns) == 12
    return t


def test_no_pk(no_pk):
    result = detect_pk(no_pk)
    assert result is None


# ---------------------------------------------------------------------------
# Dataset 4: near-miss PK — 99.8% unique (below 99.9% threshold)
# ---------------------------------------------------------------------------


@pytest.fixture
def near_miss_pk():
    ids = list(range(N))
    ids[998] = 0
    ids[999] = 1
    data = {"almost_id": ids}
    data.update(_filler_cols(N, prefix="nm"))
    data["pad"] = [i % 3 for i in range(N)]
    t = ibis.memtable(data)
    assert t.count().execute() == N
    assert len(t.columns) == 12
    return t


def test_near_miss_pk_rejected_at_default_threshold(near_miss_pk):
    result = detect_pk(near_miss_pk)
    assert result is None or result != ["almost_id"]


def test_near_miss_pk_accepted_at_lower_threshold(near_miss_pk):
    result = detect_pk(near_miss_pk, threshold=0.99)
    assert result == ["almost_id"]


# ---------------------------------------------------------------------------
# Dataset 5: multiple candidate single-column PKs
# ---------------------------------------------------------------------------


@pytest.fixture
def multiple_candidates():
    import random

    rng = random.Random(SEED + 2)
    pk_a = list(range(N))
    pk_b = list(range(N, 2 * N))
    rng.shuffle(pk_b)
    data = {"pk_a": pk_a, "pk_b": pk_b}
    fillers = _filler_cols(N, prefix="mc")
    for k, v in list(fillers.items())[:10]:
        data[k] = v
    t = ibis.memtable(data)
    assert t.count().execute() == N
    assert len(t.columns) == 12
    return t


def test_multiple_candidates_returns_first(multiple_candidates):
    result = detect_pk(multiple_candidates)
    assert result == ["pk_a"]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_table():
    t = ibis.memtable({"a": [], "b": []})
    assert detect_pk(t) is None


def test_single_row():
    t = ibis.memtable({"a": [1], "b": [2]})
    result = detect_pk(t)
    assert result == ["a"]


# ---------------------------------------------------------------------------
# Parametric: invariant checks across all 5 datasets
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
def any_dataset(request):
    return request.getfixturevalue(request.param)


def test_all_datasets_are_1000x12(any_dataset):
    assert any_dataset.count().execute() == N
    assert len(any_dataset.columns) == 12


def test_detect_pk_returns_list_or_none(any_dataset):
    result = detect_pk(any_dataset)
    assert result is None or isinstance(result, list)


def test_detect_pk_result_columns_exist_in_table(any_dataset):
    result = detect_pk(any_dataset)
    if result is not None:
        for col in result:
            assert col in any_dataset.columns
