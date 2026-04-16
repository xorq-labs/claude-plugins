"""Primary key detection for tabular data using Polars.

Ported from ~/xorq_mcp/xorq_mcp_tool.py — shared helper used by
xorq_recon and xorq_recon_import.
"""

from __future__ import annotations

from itertools import combinations
from math import prod

import polars as pl


def detect_pk(df: pl.DataFrame, threshold: float = 0.999) -> list[str] | None:
    """Two-pass polars PK detection with dirty-data tolerance.

    Samples 5K rows to fast-reject bad candidates, then full-scans survivors.
    Accepts a composite key (up to 4 columns) when n_unique/n_rows >= threshold.
    Returns the shortest qualifying key or None.
    """
    n = len(df)
    if n == 0:
        return None
    cols = df.columns
    min_distinct = threshold * n

    # Single pass: distinct count for every column
    card = {c: df[c].n_unique() for c in cols}

    # Width 1: check from precomputed cardinalities
    for c in cols:
        if card[c] / n >= threshold:
            return [c]

    # Width 2–4: with pruning
    sample = df.sample(min(5_000, n), seed=42)

    def uniqueness(frame: pl.DataFrame, col_names: list[str]) -> float:
        return frame.select(pl.struct(col_names).n_unique()).item() / len(frame)

    for width in range(2, 5):
        for combo in combinations(cols, width):
            col_names = list(combo)

            # Prune: product of individual cardinalities is the theoretical
            # max distinct count for this combo.
            if prod(card[c] for c in col_names) < min_distinct:
                continue

            if uniqueness(sample, col_names) < threshold:
                continue
            if uniqueness(df, col_names) >= threshold:
                return col_names
    return None
