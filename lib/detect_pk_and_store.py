"""Detect primary key for a catalog entry and store it in catalog metadata.

Usage:
    python detect_pk_and_store.py <catalog_path> <alias> <source_file>

Reads the source file via xorq, runs PK detection, and writes the result
into the catalog entry's .metadata.yaml as `primary_key: [col, ...]`.
"""

from __future__ import annotations

import sys
from itertools import combinations
from math import prod
from pathlib import Path

import yaml


def detect_pk_from_file(file_path: str, threshold: float = 0.999) -> list[str] | None:
    """Read a CSV/Parquet file via xorq and detect primary key."""
    import xorq.api as xo

    if file_path.endswith((".parquet", ".parq")):
        table = xo.deferred_read_parquet(file_path)
    else:
        table = xo.deferred_read_csv(file_path)

    n_total = table.count().execute()
    if n_total == 0:
        return None

    cols = table.columns
    min_distinct = threshold * n_total

    agg_exprs = [table[c].nunique().name(c) for c in cols]
    cardinality_row = table.agg(agg_exprs).execute()
    card = {c: int(cardinality_row[c].iloc[0]) for c in cols}

    for c in cols:
        if card[c] / n_total >= threshold:
            return [c]

    sample_size = min(5_000, n_total)
    sample = table.limit(sample_size) if sample_size < n_total else table
    sample_n = sample.count().execute()

    for width in range(2, 5):
        for combo in combinations(cols, width):
            col_names = list(combo)
            if prod(card[c] for c in col_names) < min_distinct:
                continue
            s_distinct = sample.select(*col_names).distinct().count().execute()
            if s_distinct / sample_n < threshold:
                continue
            f_distinct = table.select(*col_names).distinct().count().execute()
            if f_distinct / n_total >= threshold:
                return col_names
    return None


def store_pk(catalog_path: str, alias: str, pk: list[str] | None) -> None:
    """Write primary_key into catalog entry metadata."""
    from xorq.catalog.catalog import Catalog, CatalogAlias

    cat = Catalog.from_repo_path(Path(catalog_path))
    try:
        entry = CatalogAlias.from_name(alias, cat).catalog_entry
    except Exception:
        print(f"error: alias '{alias}' not found in catalog", file=sys.stderr)
        sys.exit(1)

    meta = {}
    if entry.metadata_path.exists():
        meta = yaml.safe_load(entry.metadata_path.read_text()) or {}

    if pk is not None:
        meta["primary_key"] = pk
    else:
        meta.pop("primary_key", None)

    entry.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    entry.metadata_path.write_text(yaml.dump(meta, default_flow_style=False))


def main():
    if len(sys.argv) != 4:
        print(
            f"usage: {sys.argv[0]} <catalog_path> <alias> <source_file>",
            file=sys.stderr,
        )
        sys.exit(1)

    catalog_path, alias, source_file = sys.argv[1], sys.argv[2], sys.argv[3]

    pk = detect_pk_from_file(source_file)
    store_pk(catalog_path, alias, pk)

    if pk:
        print(f"PK: {' + '.join(pk)}")
    else:
        print("PK: none detected")


if __name__ == "__main__":
    main()
