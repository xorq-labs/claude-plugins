---
description: Diagnose xorq runs and work with caching — read the always-on run logs (status, error, per-phase timings, output size), confirm cache hits/misses without any collector, and add/choose/invalidate caches. Use when a run failed, a re-run is unexpectedly slow or serves stale data, the user wants to cache a pipeline or avoid recomputing an expensive step, or asks whether/why the cache is (not) working; for composing or running entries themselves use composer / catalog-explore.
---

# Diagnose — Run Logs, Cache Hits, and Telemetry

xorq has two zero-infrastructure observability layers. **Run logs** are always on — plain JSON files
answering *did it succeed, why did it fail, which phase was slow, how big was the output*. **Cache
hit/miss** is emitted only as OTel span events, but a built-in console fallback makes it greppable
with one env var — no collector, no OTLP setup.

## Run logs (always on — read these first)

Every `xorq run`, `xorq run-cached`, and `xorq catalog run` / `compose` records itself under
`~/.local/share/xorq/runs/` (override: `XORQ_RUNS_LOGS_DIR`):

```
~/.local/share/xorq/runs/<expr_hash>/<run_id>/
  run.jsonl    # append-only events, one JSON object per line
  meta.json    # summary, written on completion
```

`<expr_hash>` is the build-directory name (content hash); `<run_id>` is a UUID per invocation.
Latest run for a build:

```bash
ls -t ~/.local/share/xorq/runs/<expr_hash> | head -1
```

- **`meta.json`** — `status` (`ok` / `error`), `error` (message, on failure), all run params
  (`expr_path`, `output_format`, `limit`, …), `started_at` / `completed_at`, `xorq_version`,
  `otel_trace_id`.
- **`run.jsonl`** — `<verb>.start` → `<verb>.expr_loaded` → `<verb>.done` → `<verb>.output_written`,
  where `<verb>` is `run` / `run_cached` / `catalog_run`. `expr_loaded` and `done` carry `elapsed_s`
  (load vs execute time — tells you *which* phase is slow); `output_written` carries `bytes` (+ `rows`
  for parquet output).

A run that errors before `done` shows the failure point by which events are missing; `meta.json.error`
has the message.

## Verify cache hit / miss

Hit/miss never appears in `run.jsonl` — it's an OTel span event (`cache.hit` / `cache.miss` on span
`cache.set_default`). With no collector configured, spans are dropped **unless** you set the console
fallback, which prints them as JSON lines to stdout:

```bash
OTEL_EXPORTER_CONSOLE_FALLBACK=1 xorq run-cached <build_path> -o /tmp/out.parquet \
  | grep -coE '"name": "cache\.hit"'
```

First run prints `0` (miss → cache populated); an identical second run prints `1` (hit). From Python,
introspect directly: `expr.ls.get_key()`, `expr.ls.exists()`, `expr.ls.get_cache_path()`.

## Adding a cache — where and which

**Where:** `.cache()` snapshots its input — place it at the *expensive prefix boundary* (after the
remote read / join / fit, before cheap downstream shaping), not on the final expression. In build
scripts: `expr = expensive_part.cache().select(...)`; in `ml` fits, pass `cache=` so recovery hits
cache instead of refitting. To cache a whole build at run time, no script change is needed:

```bash
xorq run-cached <build_path> --cache-type <type> [--ttl <seconds>] [--cache-dir <path>]
```

**Which strategy** (`xorq.caching` classes / `--cache-type`):

| You want | Strategy / class |
|----------|------------------|
| Re-run when source files **or** expr change (safe default) | `modification-time` / `ParquetCache` |
| Pin a result; refresh only when the expr changes | `snapshot` / `ParquetSnapshotCache` |
| Pin, but refresh every N seconds | `--ttl N` / `ParquetTTLSnapshotCache` |
| Cache as a table **in the source backend** (no local parquet) | `SourceCache` / `SourceSnapshotCache` |
| Share the cache across machines via GCS | `GCSCache` |

## Inspecting and invalidating

Cache files live at `<XORQ_CACHE_DIR>/parquet/letsql_cache-*.parquet` (default `~/.cache/xorq`;
snapshot keys contain `snapshot-`). There is **no `xorq cache` subcommand** — to force a refresh,
delete the entry's parquet file (its name is the key from `expr.ls.get_key()`), or the whole
`parquet/` dir to start clean.

## Full OTel (rarely needed)

Spans export over OTLP only when `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` points at a remote host or a
localhost collector that is *actually listening* — otherwise xorq falls back to console (silenced
unless `OTEL_EXPORTER_CONSOLE_FALLBACK=1`). Collector/dashboard setup is infrastructure work, out of
scope here; `meta.json.otel_trace_id` links a run to its trace if one exists.

## Pitfalls

- **Console fallback writes spans to stdout** — never combine with `-o -`; send data output to a
  file so stdout carries only spans.
- **One miss emits two `cache.miss` events** (parent span + `cache.put` child) — count `cache.hit`,
  don't count misses.
- **`--ttl` always implies the snapshot strategy** — it silently overrides
  `--cache-type modification-time`.
- **Env vars are read at first import** (`XORQ_RUNS_LOGS_DIR`, `XORQ_CACHE_DIR`) — set them on the
  command line, not mid-process.
- **Run logs are keyed by expr_hash, not alias** — resolve the alias to its hash
  (`xorq catalog list --kind`) before looking up runs.
- **A missing run dir doesn't mean the run didn't happen** — if the store can't be created
  (permissions), xorq degrades to a no-op logger and runs anyway.

## Arguments

`$ARGUMENTS`: a build path, entry alias, or expr hash to diagnose, and/or what's being investigated
(failure, slowness, staleness, cache verification).

Docs: full CLI + Python API index at <https://docs.xorq.dev/llms.txt>.
