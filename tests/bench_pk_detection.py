"""Benchmark: polars vs xorq PK detection on 1M-row, 12-column CSV files.

Generates 5 CSV files with different PK profiles, then times both
implementations on each file.

Usage:
    uv run python tests/bench_pk_detection.py
"""

from __future__ import annotations

import csv
import os
import random
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

N = 1_000_000
SEED = 42


def _filler_cols(n: int, rng: random.Random, prefix: str = "col") -> dict[str, list]:
    cols: dict[str, list] = {}
    for i in range(4):
        pool = [f"{prefix}_{i}_val_{v}" for v in range(50)]
        cols[f"{prefix}_str_{i}"] = [rng.choice(pool) for _ in range(n)]
    for i in range(3):
        cols[f"{prefix}_int_{i}"] = [rng.randint(0, 19) for _ in range(n)]
    for i in range(3):
        cols[f"{prefix}_flt_{i}"] = [round(rng.uniform(0, 100), 1) for _ in range(n)]
    return cols


def generate_files(out_dir: str) -> list[tuple[str, str, str]]:
    """Generate 5 CSV files. Returns [(path, name, expected_pk_desc), ...]."""
    rng = random.Random(SEED)
    files = []

    # 1. clear_single_pk — row_id is unique
    name = "clear_single_pk.csv"
    path = os.path.join(out_dir, name)
    data = {"row_id": list(range(N))}
    data.update(_filler_cols(N, rng))
    data["extra"] = [i % 7 for i in range(N)]
    _write_csv(path, data)
    files.append((path, name, "single PK: row_id"))

    # 2. composite_pk — group x seq unique
    name = "composite_pk.csv"
    path = os.path.join(out_dir, name)
    groups = [f"g{i // 20}" for i in range(N)]
    seqs = [i % 20 for i in range(N)]
    data = {"group": groups, "seq": seqs}
    fillers = _filler_cols(N, rng, prefix="c")
    for k, v in list(fillers.items())[:10]:
        data[k] = v
    _write_csv(path, data)
    files.append((path, name, "composite PK: group + seq"))

    # 3. no_pk — 5 distinct values per column
    name = "no_pk.csv"
    path = os.path.join(out_dir, name)
    rng2 = random.Random(SEED + 1)
    data = {}
    for i in range(6):
        pool = [f"cat_{i}_{v}" for v in range(5)]
        data[f"cat_{i}"] = [rng2.choice(pool) for _ in range(N)]
    for i in range(6):
        data[f"num_{i}"] = [rng2.randint(0, 4) for _ in range(N)]
    _write_csv(path, data)
    files.append((path, name, "no PK"))

    # 4. near_miss_pk — 99.8% unique
    name = "near_miss_pk.csv"
    path = os.path.join(out_dir, name)
    ids = list(range(N))
    n_dupes = int(N * 0.002)
    for i in range(n_dupes):
        ids[N - 1 - i] = i
    data = {"almost_id": ids}
    data.update(_filler_cols(N, rng, prefix="nm"))
    data["pad"] = [i % 3 for i in range(N)]
    _write_csv(path, data)
    files.append((path, name, "near-miss PK (99.8% unique)"))

    # 5. multiple_candidates — two unique columns
    name = "multiple_candidates.csv"
    path = os.path.join(out_dir, name)
    rng3 = random.Random(SEED + 2)
    pk_a = list(range(N))
    pk_b = list(range(N, 2 * N))
    rng3.shuffle(pk_b)
    data = {"pk_a": pk_a, "pk_b": pk_b}
    fillers = _filler_cols(N, rng, prefix="mc")
    for k, v in list(fillers.items())[:10]:
        data[k] = v
    _write_csv(path, data)
    files.append((path, name, "multiple PKs: pk_a first"))

    return files


def _write_csv(path: str, data: dict[str, list]) -> None:
    cols = list(data.keys())
    n = len(data[cols[0]])
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(cols)
        for i in range(n):
            writer.writerow([data[c][i] for c in cols])


def bench_polars(files: list[tuple[str, str, str]]) -> list[dict]:
    import polars as pl
    from pk_detection import detect_pk

    results = []
    for path, name, expected in files:
        t0 = time.perf_counter()
        df = pl.read_csv(path, infer_schema_length=1000)
        t_load = time.perf_counter() - t0

        t1 = time.perf_counter()
        pk = detect_pk(df)
        t_detect = time.perf_counter() - t1

        results.append(
            {
                "name": name,
                "engine": "polars",
                "load_s": round(t_load, 3),
                "detect_s": round(t_detect, 3),
                "total_s": round(t_load + t_detect, 3),
                "pk": pk,
                "expected": expected,
            }
        )
    return results


def bench_xorq(files: list[tuple[str, str, str]]) -> list[dict]:
    import xorq.api as xo
    from pk_detection_xorq import detect_pk

    results = []
    for path, name, expected in files:
        t0 = time.perf_counter()
        table = xo.deferred_read_csv(path)
        t_load = time.perf_counter() - t0

        t1 = time.perf_counter()
        pk = detect_pk(table)
        t_detect = time.perf_counter() - t1

        results.append(
            {
                "name": name,
                "engine": "xorq",
                "load_s": round(t_load, 3),
                "detect_s": round(t_detect, 3),
                "total_s": round(t_load + t_detect, 3),
                "pk": pk,
                "expected": expected,
            }
        )
    return results


def print_results(polars_results: list[dict], xorq_results: list[dict]) -> None:
    header = (
        f"{'file':<28} {'engine':<8} {'load':>7} {'detect':>8} {'total':>8}  {'pk'}"
    )
    print(f"\n{'=' * 90}")
    print(f"PK detection benchmark — {N:,} rows x 12 columns")
    print(f"{'=' * 90}")
    print(header)
    print("-" * 90)

    for p, x in zip(polars_results, xorq_results):
        print(
            f"{p['name']:<28} {'polars':<8} {p['load_s']:>6.3f}s {p['detect_s']:>7.3f}s {p['total_s']:>7.3f}s  {p['pk']}"
        )
        print(
            f"{'':<28} {'xorq':<8} {x['load_s']:>6.3f}s {x['detect_s']:>7.3f}s {x['total_s']:>7.3f}s  {x['pk']}"
        )
        speedup = p["detect_s"] / x["detect_s"] if x["detect_s"] > 0 else float("inf")
        winner = "xorq" if speedup > 1 else "polars"
        print(f"{'':<28} {'':>8} {'':>7} {f'  {speedup:.1f}x ({winner})':>20}")
        print()

    # Summary
    total_polars = sum(r["detect_s"] for r in polars_results)
    total_xorq = sum(r["detect_s"] for r in xorq_results)
    print("-" * 90)
    print(f"{'Total detect time':<28} {'polars':<8} {'':>7} {total_polars:>7.3f}s")
    print(f"{'':28} {'xorq':<8} {'':>7} {total_xorq:>7.3f}s")
    overall = total_polars / total_xorq if total_xorq > 0 else float("inf")
    winner = "xorq" if overall > 1 else "polars"
    print(f"{'':28} {'':>8} {'':>7} {f'  {overall:.1f}x ({winner})':>20}")
    print(f"{'=' * 90}")


def main():
    tmpdir = tempfile.mkdtemp(prefix="pk_bench_")
    print(f"Generating 5 x {N:,}-row CSV files in {tmpdir} ...")

    t0 = time.perf_counter()
    files = generate_files(tmpdir)
    gen_time = time.perf_counter() - t0
    total_mb = sum(os.path.getsize(f[0]) for f in files) / 1024 / 1024
    print(f"Generated {total_mb:.0f} MB in {gen_time:.1f}s\n")

    for path, name, _ in files:
        mb = os.path.getsize(path) / 1024 / 1024
        print(f"  {name:<28} {mb:>6.1f} MB")

    print("\nRunning polars benchmark ...")
    polars_results = bench_polars(files)

    print("Running xorq benchmark ...")
    xorq_results = bench_xorq(files)

    # Verify both agree on PKs
    for p, x in zip(polars_results, xorq_results):
        if p["pk"] != x["pk"]:
            print(
                f"\nWARNING: disagreement on {p['name']}: polars={p['pk']} xorq={x['pk']}"
            )

    print_results(polars_results, xorq_results)

    # Cleanup
    print(f"\nBenchmark files in: {tmpdir}")
    print("(delete manually when done)")


if __name__ == "__main__":
    main()
