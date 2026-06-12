"""Contract tests for the `diagnose` skill's observability claims.

Pins what xorq/skills/diagnose/SKILL.md claims, so doc drift is caught:
  - `xorq run` writes a run log under <runs dir>/<expr_hash>/<run_id>/ with
    meta.json (status, params) and run.jsonl (start -> expr_loaded -> done,
    each timed phase carrying elapsed_s)
  - a failing run finalizes meta.json with status=error + the error message
  - XORQ_RUNS_LOGS_DIR overrides the run-log store location
  - cache hit/miss is observable with OTEL_EXPORTER_CONSOLE_FALLBACK=1 alone
    (no collector): first run-cached -> no cache.hit, second -> cache.hit;
    a miss emits TWO cache.miss events (parent + cache.put child), so hits
    are what you count
  - cache files land at <HOME>/.cache/xorq/parquet/letsql_cache-*.parquet,
    `--ttl` switches the key to the snapshot strategy, and deleting the
    parquet file invalidates (next run is a miss again)

Uses the hermetic `xorq` fixture (temp HOME) so run logs and cache dirs are
per-test. Verified against xorq 0.3.29.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"

CONSOLE_FALLBACK = {"OTEL_EXPORTER_CONSOLE_FALLBACK": "1"}


def _build(xorq, tmp_path: Path, script_src: str) -> Path:
    (tmp_path / "expr.py").write_text(script_src)
    bp = tmp_path / "bp.txt"
    r = xorq.run(
        "build", str(tmp_path / "expr.py"),
        "--builds-dir", str(tmp_path / "builds_diag"),
        "--emit-build-path-to", str(bp),
    )
    assert r.code == 0, r.output
    return Path(bp.read_text().strip())


def _csv_build(xorq, tmp_path: Path) -> Path:
    src = f"import xorq.api as xo\nexpr = xo.deferred_read_csv({str(DATA / 'customers.csv')!r})\n"
    return _build(xorq, tmp_path, src)


def _runs_dir(xorq) -> Path:
    # SKILL.md: ~/.local/share/xorq/runs (hermetic HOME)
    return xorq.home / ".local" / "share" / "xorq" / "runs"


def _latest_run_dir(runs_dir: Path, expr_hash: str) -> Path:
    expr_dir = runs_dir / expr_hash
    assert expr_dir.is_dir(), f"no run logs for {expr_hash} under {runs_dir}"
    return max(expr_dir.iterdir(), key=lambda p: p.stat().st_mtime)


def _events(run_dir: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (run_dir / "run.jsonl").read_text().splitlines()
        if line.strip()
    ]


def _cache_hits(output: str) -> int:
    return output.count('"name": "cache.hit"')


def _cache_misses(output: str) -> int:
    return output.count('"name": "cache.miss"')


def test_run_writes_run_log_with_phases(xorq, tmp_path: Path) -> None:
    build = _csv_build(xorq, tmp_path)
    r = xorq.run("run", str(build), "-o", "-", "-f", "json", "--limit", "2")
    assert r.code == 0, r.output

    run_dir = _latest_run_dir(_runs_dir(xorq), build.name)
    meta = json.loads((run_dir / "meta.json").read_text())
    assert meta["status"] == "ok"
    assert meta["expr_hash"] == build.name
    assert meta["run_id"] == run_dir.name

    events = {e["event"]: e for e in _events(run_dir)}
    assert {"run.start", "run.expr_loaded", "run.done"} <= events.keys()
    # the timed phases carry elapsed_s (load vs execute diagnosis)
    assert events["run.expr_loaded"]["elapsed_s"] >= 0
    assert events["run.done"]["elapsed_s"] >= 0


def test_failed_run_finalizes_meta_with_error(xorq, tmp_path: Path) -> None:
    csv = tmp_path / "vanishing.csv"
    csv.write_text((DATA / "customers.csv").read_text())
    build = _build(
        xorq, tmp_path,
        f"import xorq.api as xo\nexpr = xo.deferred_read_csv({str(csv)!r})\n",
    )
    csv.unlink()  # make execution fail after a successful build

    r = xorq.run("run", str(build), "-o", "-", "-f", "json", "--limit", "2")
    assert r.code != 0

    meta = json.loads(
        (_latest_run_dir(_runs_dir(xorq), build.name) / "meta.json").read_text()
    )
    assert meta["status"] == "error"
    assert meta.get("error")


def test_runs_logs_dir_env_override(xorq, tmp_path: Path) -> None:
    build = _csv_build(xorq, tmp_path)
    override = tmp_path / "my_runs"
    r = xorq.run(
        "run", str(build), "-o", "-", "-f", "json", "--limit", "2",
        env={"XORQ_RUNS_LOGS_DIR": str(override)},
    )
    assert r.code == 0, r.output
    assert (override / build.name).is_dir()


def test_run_cached_hit_miss_via_console_fallback(xorq, tmp_path: Path) -> None:
    build = _csv_build(xorq, tmp_path)
    out = tmp_path / "out.parquet"

    first = xorq.run(
        "run-cached", str(build), "-o", str(out), env=CONSOLE_FALLBACK
    )
    assert first.code == 0, first.output
    assert _cache_hits(first.output) == 0
    # SKILL.md pitfall: one miss emits TWO cache.miss events (parent + cache.put)
    assert _cache_misses(first.output) == 2

    second = xorq.run(
        "run-cached", str(build), "-o", str(out), env=CONSOLE_FALLBACK
    )
    assert second.code == 0, second.output
    assert _cache_hits(second.output) == 1
    assert _cache_misses(second.output) == 0

    # run_cached.* events land in the run log too
    events = {e["event"] for e in _events(_latest_run_dir(_runs_dir(xorq), build.name))}
    assert {"run_cached.start", "run_cached.done"} <= events


def test_cache_files_ttl_key_and_invalidation(xorq, tmp_path: Path) -> None:
    build = _csv_build(xorq, tmp_path)
    out = tmp_path / "out.parquet"
    cache_dir = xorq.home / ".cache" / "xorq"

    r = xorq.run("run-cached", str(build), "-o", str(out))
    assert r.code == 0, r.output
    cached = list((cache_dir / "parquet").glob("letsql_cache-*.parquet"))
    assert len(cached) == 1
    assert "snapshot" not in cached[0].name  # default: modification-time keys

    # --ttl implies the snapshot strategy regardless of --cache-type
    r = xorq.run("run-cached", str(build), "-o", str(out), "--ttl", "3600")
    assert r.code == 0, r.output
    assert any(
        "snapshot" in p.name
        for p in (cache_dir / "parquet").glob("letsql_cache-*.parquet")
    )

    # no `xorq cache` subcommand: invalidation = delete the parquet file
    cached[0].unlink()
    r = xorq.run("run-cached", str(build), "-o", str(out), env=CONSOLE_FALLBACK)
    assert r.code == 0, r.output
    assert _cache_misses(r.output) == 2 and _cache_hits(r.output) == 0
