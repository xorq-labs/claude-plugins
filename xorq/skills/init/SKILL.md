---
description: Ingest CSV or Parquet files into a xorq catalog. Use when the user wants to onboard raw data files, create catalog entries from local files, or set up a new data source with an alias.
---

# Init — Ingest Data Files into a Catalog

Guide the user through turning raw CSV/Parquet files into cataloged, versioned xorq expressions.

## When to use / When NOT to use

- **Use when**: the user has raw `.csv` / `.parquet` files they want to register as cataloged sources.
- **Not when**:
  - They want to join existing catalog entries → `/xorq:composer`.
  - They want to fit a model or tag an expression (BSL / ML / custom TagHandler) → `/xorq:builder`.
  - They want to inspect what's already cataloged → `/xorq:catalog-explore`.

## Workflow

### 0. Resolve target catalog

Before anything else, run the **Catalog Resolution** procedure from `xorq/CLAUDE.md` — glob the repo for an existing `catalog.yaml`, and if none is found, ask the user whether to create `<repo-name>-catalog` (yes) or fall back to the system default (no). Record the resolved name/path; all subsequent `xorq catalog …` commands in this skill assume it.

### 1. Verify the resolved catalog

```bash
xorq catalog info
```

If Step 0 created a fresh catalog, this confirms it. Remotes are **opt-in** — only attach one if the user explicitly wants to share/push builds to a git repo (see `xorq catalog init --help`).

### 2. Write an ingestion script

Create a small Python script that reads the data file. The script must define an `expr` variable (the default name `xorq build` captures).

**For CSV files:**

```python
import xorq.api as xo

expr = xo.deferred_read_csv("/absolute/path/to/data.csv")
```

**For Parquet files:**

```python
import xorq.api as xo

expr = xo.deferred_read_parquet("/absolute/path/to/data.parquet")
```

**With transforms (optional):**

```python
import xorq.api as xo
from xorq.api import _

expr = xo.deferred_read_csv("/absolute/path/to/data.csv")
expr = expr.filter(_.amount > 0).select("id", "amount", "category")
```

Use absolute paths for data files.

### 3. Build the script

```bash
xorq build <script.py> --builds-dir builds_source
```

This produces artifacts under `builds_source/<hash>/`. Note the build path from the output. Using `--builds-dir builds_source` prevents hash collisions with builder entries (ML, BSL, custom tags) that wrap the same source data — see CLAUDE.md "Build hash collision" pitfall.

### 4. Add to catalog with alias

```bash
xorq catalog add builds_source/<hash> --alias <name>
```

The alias gives the entry a human-readable name. Multiple aliases can be added:

```bash
xorq catalog add builds/<hash> --alias <name1> --alias <name2>
```

### 5. Verify

List entries to confirm:

```bash
xorq catalog list --kind
```

Inspect the schema:

```bash
xorq catalog schema <alias> --json
```

## Batch ingestion

When ingesting multiple files, write one script per file and build/add each sequentially — run `xorq build`, note the printed build path, then `xorq catalog add` that path with an alias:

```bash
xorq build ingest_customers.py --builds-dir builds_source
# output prints the build path, e.g. builds_source/abc123...
xorq catalog add builds_source/<hash> --alias customers

xorq build ingest_orders.py --builds-dir builds_source
xorq catalog add builds_source/<hash> --alias orders
```

Add `--no-sync` to `catalog add` if you want to defer pushing to the remote until the batch is complete.

## pyproject.toml setup

**IMPORTANT:** If `xorq catalog add` fails with `Multiple top-level packages discovered in a flat-layout`, add this to `pyproject.toml`:

```toml
[tool.setuptools]
py-modules = []
```

This prevents setuptools from auto-discovering data directories as Python packages.

## Tips

- See CLAUDE.md Common Pitfalls for environment and API issues (VIRTUAL_ENV mismatch, flat-layout error, etc.)
- Use `--no-sync` on `catalog add` if working without a remote: `xorq catalog add builds/<hash> --alias <name> --no-sync`
- When the resolved catalog is **repo-local with no remote configured**, prefer `--no-sync` by default on every `catalog add` — there's no upstream to push to, and the sync attempt is wasted work.
- The `expr` variable name is the default. Use `-e <name>` with `xorq build` if the script uses a different variable name.
- Use `xorq build --debug` to output SQL files for inspection.
- After adding, the entry kind should be `Source` (visible with `--kind` flag on list).
- Use **absolute paths** for data files in scripts to avoid path resolution issues.
- `xorq build` output shows the build hash — capture it for the `catalog add` step.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as file path(s) to ingest.
