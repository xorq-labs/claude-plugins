---
description: Originate new Source catalog entries from raw data — local .csv / .parquet files, or tables from DuckDB / SQLite / Postgres — by building them. Use when data isn't catalogued anywhere yet and must be created from scratch.
---

# Ingest — Originate a Source from Raw Data (build)

Mint versioned **`Source`** catalog entries from raw data — a local file (`.csv`, `.parquet`) or a
table in a backend (DuckDB, SQLite, Postgres). Each entry is a content-addressed build artifact: a
reproducible pointer downstream work can compose, run, and build on. (If the data is already catalogued
elsewhere, *acquire* that entry instead of rebuilding — that's xorq's own catalog machinery, not this
skill.)

The flow is **BUILD-ADD** with a one-line ingest expression bound to `expr`; only that expression
changes per source. Builds are cheap for files (a `deferred_read_*` records the path; nothing is
copied). Use `--builds-dir builds_source` so ingest builds can't collide; the entry's kind is `source`.


## Source templates — bind `expr`, then BUILD-ADD

Use **absolute paths** for files and DB files — the build hash embeds the path, so absolute paths keep
entries runnable from any working directory. **DB drivers aren't bundled** — install the extra
(`pip install 'xorq[duckdb]'` / `[sqlite]` / `[postgres]`), else `xo.duckdb` / `xo.sqlite` / `xo.postgres`
raise `ModuleNotFoundError`.

| Source | `expr = …` (in a build script) | Note |
|---|---|---|
| CSV / Parquet | `xo.deferred_read_csv("/abs/data.csv")` / `xo.deferred_read_parquet("/abs/data.parquet")` | records the path; nothing materialized |
| SQLite | `xo.sqlite.connect("/abs/app.db").table("<t>")` | connection embedded as a profile |
| DuckDB | `xo.duckdb.connect("/abs/wh.duckdb").table("<t>")` | also materializes the table to `database_tables/*.parquet` |
| Postgres | a `Profile` with `${VAR}` refs (below) | refs recorded, not secrets — safe to commit |

**Postgres** — build the connection from a `Profile` whose secrets are **`${VAR}` references**, never
literals. The build records the references; values resolve from the environment at build and at every
`run`:

```python
import xorq.api as xo
from xorq.vendor.ibis.backends.profiles import Profile
profile = Profile(con_name="postgres", kwargs_tuple=(
    ("host", "${POSTGRES_HOST}"), ("port", 5432), ("database", "${POSTGRES_DB}"),
    ("user", "${POSTGRES_USER}"), ("password", "${POSTGRES_PASSWORD}"),
))
expr = profile.get_con().table("<table_name>")   # ${...} resolved here, from the environment
```

> **Never** pass a literal password to a `Profile` / `connect(...)` for an entry you catalog — it would
> be written into the build's `profiles.yaml`. Set the referenced vars before building/running.

## Verify

Run **VERIFY**; expect `source`. Preview rows (`-o -` required):

```bash
xorq catalog list --kind                          # expect: <hash>  source
xorq run "$(cat bp.txt)" -o - -f json --limit 5             # current env, universal
xorq catalog run <alias> --use-this-venv -o - -f json --limit 5
```

**DuckDB caveat (0.3.28):** `catalog run` can't locate a materialized DuckDB entry's parquet — preview
those with `xorq run "$(cat bp.txt)"` instead.

## Several sources

One `source` entry per file/table — run BUILD-ADD once per source, **one `catalog add` at a time**.

## Pitfalls (ingest-specific; shared ones are in the kernel)

- **Missing driver** → install the `xorq[<backend>]` extra (above).
- **`catalog add` environment** → run from a dir with `pyproject.toml` + a lockfile (`uv.lock` /
  `requirements.txt`) in a wheel-buildable env (`No module named 'packaging'` means it can't build wheels).
- **Relative paths** → builds embed the path; use absolute paths for portable entries.
- **Postgres secrets** → `${VAR}` references in the `Profile`, never literal passwords.
- **DuckDB `catalog run`** → broken for materialized entries in 0.3.28; preview with `xorq run`.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the data path(s) or DB table(s) to ingest,
and/or the target catalog (`-p` / `-n`).
