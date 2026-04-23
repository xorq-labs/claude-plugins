---
description: Ingest CSV or Parquet files into a xorq catalog. Use when the user wants to onboard raw data files, create catalog entries from local files, or set up a new data source with an alias.
---

# Init — Ingest Data Files into a Catalog

Guide the user through turning raw CSV/Parquet files into cataloged, versioned xorq expressions.

## Workflow

### 1. Ensure a catalog exists

Check if a catalog is already initialized:

```bash
xorq catalog info
```

If no catalog exists, initialize one:

```bash
xorq catalog init
```

Optionally with a remote:

```bash
xorq catalog init --remote-url <git-url>
```

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
xorq build <script.py>
```

This produces artifacts under `builds/<hash>/`. Note the build path from the output.

### 4. Add to catalog with alias

```bash
xorq catalog add builds/<hash> --alias <name>
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

When ingesting multiple files, write one script per file and build/add them sequentially. You can use a loop pattern:

```bash
for script in ingest_*.py; do
  hash=$(xorq build "$script" 2>&1 | grep -oP 'builds/\K[a-f0-9]+')
  xorq catalog add "builds/$hash" --alias "$(basename "$script" .py | sed 's/ingest_//')" --no-sync
done
```

Or build all scripts first, then add:

```bash
xorq build ingest_customers.py
# note the hash from output, then:
xorq catalog add builds/<hash> --alias customers --no-sync
```

## pyproject.toml setup

**IMPORTANT:** If `xorq catalog add` fails with `Multiple top-level packages discovered in a flat-layout`, add this to `pyproject.toml`:

```toml
[tool.setuptools]
py-modules = []
```

This prevents setuptools from auto-discovering data directories as Python packages.

## Tips

- Use `--no-sync` on `catalog add` if working without a remote: `xorq catalog add builds/<hash> --alias <name> --no-sync`
- The `expr` variable name is the default. Use `-e <name>` with `xorq build` if the script uses a different variable name.
- Use `xorq build --debug` to output SQL files for inspection.
- After adding, the entry kind should be `Source` (visible with `--kind` flag on list).
- Use **absolute paths** for data files in scripts to avoid path resolution issues.
- `xorq build` output shows the build hash — capture it for the `catalog add` step.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as file path(s) to ingest.
