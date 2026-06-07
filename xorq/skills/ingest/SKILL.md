---
description: Originate new Source catalog entries from raw data — local .csv / .parquet files, or tables from DuckDB / SQLite / Postgres — by building them. Use when data isn't catalogued anywhere yet and must be created from scratch.
---

# Ingest — Originate a Source from Raw Data (build)

Mint versioned **`Source`** catalog entries from raw data: a local file (`.csv`,
`.parquet`) or a table in a database backend (DuckDB, SQLite, Postgres). Each entry is a
content-addressed **build artifact** — a reproducible pointer to the data that downstream
work can compose, run, and build on.

> **Do you even need to build?** A catalog entry is built **once**, then distributed
> git-style. If the data is already catalogued somewhere, *acquire* the entry instead —
> clone / submodule / pull / replay, no build — see the **`init`** skill. Build (this
> skill) only to **originate** a net-new `Source` that exists in no catalog yet.

Every entry is created by the same CLI flow: **write a one-line ingest expression →
`xorq build` → `xorq catalog add`**. Builds are cheap for files (a `deferred_read_*`
records the path; no data is copied). Only the per-source expression changes — the
templates below cover each backend.

## Prerequisites

- **`xorq` on PATH**, run from a project root with a `pyproject.toml` **and** a lockfile
  (`uv.lock` or `requirements.txt`) in a wheel-buildable environment — `catalog add`
  snapshots that environment into the entry. (`No module named 'packaging'` → the env
  can't build wheels.)
- **Database drivers are not bundled** — install the extra for the backend (skip for files):
  ```bash
  pip install 'xorq[duckdb]'      # or 'xorq[sqlite]' / 'xorq[postgres]'
  ```
  Without it, `xo.duckdb` / `xo.sqlite` / `xo.postgres` raise `ModuleNotFoundError`.

## The flow (same for every source)

1. **Resolve the target catalog** — run the **Catalog Resolution** procedure in
   `xorq/CLAUDE.md` first. Pass it explicitly with `-p <path>` (or `-n <name>`) on every
   command — the global flag comes **before** the subcommand. Below uses `CAT=<catalog>`.
2. **Write the ingest expression** — a tiny script binding the source to a variable named
   `expr` (pick the template for your source below). `xorq build` captures `expr` by
   default; use `-e <name>` to override.
3. **Build, then add — one entry at a time:**
   ```bash
   CAT=<catalog>                      # e.g. ./my-catalog   (or target by name: -n <name>)
   xorq build ingest.py --builds-dir builds_source --emit-build-path-to bp.txt
   xorq catalog -p "$CAT" add "$(cat bp.txt)" -a <alias>
   ```
   - `--builds-dir builds_source` isolates ingest builds so their hash can't collide.
   - `--emit-build-path-to bp.txt` captures the build path without parsing stdout (a
     harmless local-path `UserWarning` may also print).
   - `-a <alias>` names the entry (created with `kind=source`).
   - **Add one entry at a time** — the catalog's git / git-annex ops are not
     concurrency-safe; concurrent adds corrupt each other. Chain with `&&` if scripting.

## Source templates

Use **absolute paths** for files and DB files — the build hash embeds the path, so
absolute paths keep entries reproducible and runnable from any working directory.

**CSV / Parquet** — `deferred_read_*` records the path; nothing is materialized:
```python
# ingest.py
import xorq.api as xo
expr = xo.deferred_read_csv("/abs/path/to/data.csv")
# expr = xo.deferred_read_parquet("/abs/path/to/data.parquet")
```

**SQLite / DuckDB** — `.table(...)` on a backend connection (a DuckDB build also
materializes the table to `database_tables/*.parquet`):
```python
import xorq.api as xo
con = xo.sqlite.connect("/abs/path/to/app.db")
# con = xo.duckdb.connect("/abs/path/to/warehouse.duckdb")
expr = con.table("<table_name>")
```

**Postgres** — build the connection from a `Profile` whose secrets are
**environment-variable references** (`${VAR}`), never literal values. The build records
the references — not the secret — so the entry is safe to commit; values are resolved
from the environment at build time and at every `run`:
```python
import xorq.api as xo
from xorq.vendor.ibis.backends.profiles import Profile

profile = Profile(con_name="postgres", kwargs_tuple=(
    ("host",     "${POSTGRES_HOST}"),
    ("port",     5432),
    ("database", "${POSTGRES_DB}"),
    ("user",     "${POSTGRES_USER}"),
    ("password", "${POSTGRES_PASSWORD}"),
))
con = profile.get_con()        # ${...} resolved here, from the environment
expr = con.table("<table_name>")
```
Set the referenced variables in your environment before building/running:
```bash
export POSTGRES_HOST=… POSTGRES_DB=… POSTGRES_USER=… POSTGRES_PASSWORD=…
```
> **Never** pass a literal password to `xo.postgres.connect(password="…")` for an entry
> you intend to catalog — that value would be written into the build's `profiles.yaml`.

## Verify and preview

```bash
xorq catalog -p "$CAT" list --kind           # expect: <hash>  source
xorq catalog -p "$CAT" schema <alias> --json
```

Preview rows — **`-o -` is required** (output defaults to `/dev/null`):
- **Universal** (current environment): `xorq run "$(cat bp.txt)" -o - -f json --limit 5`
- **From the catalog** — `catalog run` reconstructs the entry's pinned environment in an
  isolated `uv tool run`; add `--use-this-venv` to use the current environment instead
  (correct when it already has the needed driver):
  ```bash
  xorq catalog -p "$CAT" run <alias> --use-this-venv -o - -f json --limit 5
  ```
- **DuckDB caveat (0.3.28):** `catalog run` can't locate a DuckDB entry's materialized
  parquet — preview DuckDB entries with `xorq run "$(cat bp.txt)"` instead.

## Several sources at once

One `Source` entry per file/table, added sequentially:
```bash
CAT=<catalog>
for f in <name1> <name2> <name3>; do
  printf 'import xorq.api as xo\nexpr = xo.deferred_read_csv("%s/data/%s.csv")\n' "$PWD" "$f" > ingest_$f.py
  xorq build ingest_$f.py --builds-dir builds_source --emit-build-path-to bp.txt
  xorq catalog -p "$CAT" add "$(cat bp.txt)" -a "$f"
done
xorq catalog -p "$CAT" list --kind
```

## Pitfalls

- **Missing driver** → install the `xorq[<backend>]` extra (Prerequisites).
- **`catalog add` env** → run from a dir with `pyproject.toml` + lockfile in a
  wheel-buildable environment (`No module named 'packaging'` means it can't).
- **Relative paths** → builds embed the path; use absolute paths for portable entries.
- **Parallel adds corrupt the catalog** → add one entry at a time.
- **Postgres secrets** → use `${VAR}` references in the `Profile`, never literal passwords.
- **DuckDB `catalog run`** → broken for materialized entries in 0.3.28; use `xorq run`.
- **`VIRTUAL_ENV` mismatch** (`VIRTUAL_ENV=… does not match …`) → prefix uv/xorq commands
  with `uv run --active`.

## Docs

The full, machine-readable index of every CLI command and Python API is at
<https://docs.xorq.dev/llms.txt>.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the data path(s) or DB
table(s) to ingest, and/or the target catalog (`-p`/`-n`).
