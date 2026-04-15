---
description: Bulk-import CSV and Parquet files into a xorq catalog. Use when the user wants to load local data files into a catalog for versioning and sharing.
---

# Recon Import

Import CSV and Parquet files into a xorq catalog using CLI commands.

**Do NOT use MCP tools for this workflow.** Use Bash to run `uvx xorq` CLI commands directly.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a glob pattern or directory path for the files to import.

## Required information

Before starting, you need:
1. **File pattern** — which files to import (e.g. `data/*.csv`, a directory path, or specific files)
2. **Catalog path** — where the catalog lives (e.g. `./catalog`). If no catalog exists at the path, initialize one with `uvx xorq catalog --path <path> init`.

If the user doesn't specify these, ask.

## Workflow

### 1. Discover files

Use the Glob tool or `ls` to find CSV (`.csv`) and Parquet (`.parquet`) files matching the pattern.

### 2. Check for duplicates

```bash
uvx xorq catalog --path <catalog_path> list-aliases
```

The alias for each file is its filename stem (e.g. `sales.csv` -> `sales`, `my-data.parquet` -> `my_data`). Convert hyphens to underscores. Skip files whose alias already exists in the catalog.

### 3. Import each new file

For each file, do all three steps before moving to the next file:

#### a. Write a temp Python script

Create a temp `.py` file with the import expression. Use **`xo.deferred_read_csv`** or **`xo.deferred_read_parquet`** — NOT `xo.read_csv` or `xo.read_parquet` (those produce non-portable catalog entries with absolute paths that break when the build directory is deleted or the catalog is cloned).

For CSV:
```python
import xorq.api as xo
expr = xo.deferred_read_csv("<absolute_path_to_file>")
```

For Parquet:
```python
import xorq.api as xo
expr = xo.deferred_read_parquet("<absolute_path_to_file>")
```

Always use the **absolute path** to the source file in the script.

#### b. Build

```bash
BUILD_PATH=$(uvx xorq build <script_path> -e expr 2>/dev/null | tail -1)
```

Capture the last line of stdout — that's the build directory path. The `2>/dev/null` suppresses pip/package noise from `uvx`.

#### c. Add to catalog

```bash
uvx xorq catalog --path <catalog_path> add "$BUILD_PATH" --alias <alias>
```

### 4. Verify each import

After adding, verify the schema:

```bash
uvx xorq catalog --path <catalog_path> schema <alias>
```

Confirm the columns and types look correct.

### 5. Clean up

Remove the temp Python scripts you created.

### 6. Summarize

Report:
- **Imported**: list of aliases successfully added
- **Skipped**: list of files skipped (already existed)
- **Failed**: list of files that failed (with error)
