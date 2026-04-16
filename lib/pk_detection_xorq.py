"""Primary key detection for tabular data using xorq (DataFusion backend).

Same algorithm as pk_detection.py (polars version) but expressed entirely
in xorq/ibis so it runs on the DataFusion engine without materializing
data into Python.
"""

from __future__ import annotations

from itertools import combinations
from math import prod

from xorq.vendor.ibis.expr.types import Table


def detect_pk(table: Table, threshold: float = 0.999) -> list[str] | None:
    """Two-pass xorq/ibis PK detection with dirty-data tolerance.

    Optimizations over the naive per-combo approach:
    1. Single agg query for all individual column cardinalities.
    2. Width-1 check uses those results directly — no extra queries.
    3. Theoretical-max pruning: skip any combo whose max possible
       distinct count (product of individual cardinalities) can't
       reach threshold * n_rows.
    4. Sample-based fast-reject for surviving combos.
    5. Full-scan only for combos that pass the sample check.

    Returns the shortest qualifying key or None.
    """
    n_total = table.count().execute()
    if n_total == 0:
        return None

    cols = table.columns
    min_distinct = threshold * n_total

    # --- Single query: distinct count for every column ---
    agg_exprs = [table[c].nunique().name(c) for c in cols]
    cardinality_row = table.agg(agg_exprs).execute()
    card = {c: int(cardinality_row[c].iloc[0]) for c in cols}

    # --- Width 1: check from precomputed cardinalities ---
    for c in cols:
        if card[c] / n_total >= threshold:
            return [c]

    # --- Width 2–4: with pruning ---
    sample_size = min(5_000, n_total)
    if sample_size < n_total:
        sample = table.limit(sample_size)
    else:
        sample = table
    sample_n = sample.count().execute()

    for width in range(2, 5):
        for combo in combinations(cols, width):
            col_names = list(combo)

            # Prune: product of individual cardinalities is the theoretical
            # max distinct count for this combo. If it can't reach the
            # threshold, skip without querying.
            max_possible = prod(card[c] for c in col_names)
            if max_possible < min_distinct:
                continue

            # Sample fast-reject
            s_distinct = sample.select(*col_names).distinct().count().execute()
            if s_distinct / sample_n < threshold:
                continue

            # Full scan
            f_distinct = table.select(*col_names).distinct().count().execute()
            if f_distinct / n_total >= threshold:
                return col_names

    return None
