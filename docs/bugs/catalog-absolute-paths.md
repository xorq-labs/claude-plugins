# Non-obvious how to create portable catalog entries from CSV files

## The problem

When importing CSV (or Parquet) files into a xorq catalog, the natural approach — `xo.read_csv()` — produces catalog entries that only work on the machine where they were built. The data is snapshotted into the catalog zip, but the expression references it with an absolute path that breaks when the catalog is cloned elsewhere or the build directory is cleaned up.

It's not clear from the docs or API what the correct way to create portable catalog entries from local files is. We found a workaround through `ibis.memtable()`, but we're not sure if that's the intended approach.

## Tested with

- xorq 0.3.19 (latest on PyPI as of 2026-04-15)
- Reproduction in `tests/bug-repro/` (machine-a builds + catalogs, machine-b clones via git)

## Steps to reproduce

```bash
rm -rf /tmp/xorq-repro

export MAC_A="/tmp/xorq-repro/machine-a"
export A_BUILDS="$MAC_A/builds"
export A_CAT="$MAC_A/catalog"
export B_CAT="/tmp/xorq-repro/machine-b/catalog"

# 1. Create a CSV
mkdir -p $MAC_A
cat > $MAC_A/sales.csv << 'CSV'
date,product,quantity,price
2026-01-01,Widget,10,29.99
2026-01-02,Gadget,5,49.99
2026-01-03,Widget,8,29.99
CSV

# 2. Create a build script using read_csv
cat > $MAC_A/import.py << 'PYEOF'
import xorq.api as xo
expr = xo.read_csv("/tmp/xorq-repro/machine-a/sales.csv")
PYEOF

# 3. Build and add to catalog
export BUILD_PATH=$(uvx xorq build $MAC_A/import.py -e expr --builds-dir $A_BUILDS 2>/dev/null | tail -1)
uvx xorq catalog --path $A_CAT init
uvx xorq catalog --path $A_CAT add "$BUILD_PATH" --alias sales

# 4. Verify it works locally
uvx xorq catalog --path $A_CAT run sales --limit 3 -f csv -o /dev/stdout
# => works fine

# 5. Clone the catalog (simulating another machine)
git clone $A_CAT $B_CAT

# 6. Clone works because the build dir still exists on this machine
uvx xorq catalog --path $B_CAT run sales --limit 3 -f csv -o /dev/stdout
# => works! the absolute path in expr.yaml still resolves

# 7. Delete the build dir — now BOTH catalogs break
rm -rf $A_BUILDS
uvx xorq catalog --path $A_CAT run sales --limit 3 -f csv -o /dev/stdout
# => Error: At least one path is required
uvx xorq catalog --path $B_CAT run sales --limit 3 -f csv -o /dev/stdout
# => Error: At least one path is required

# 8. Metadata operations still work (they don't execute the expression)
uvx xorq catalog --path $B_CAT list-aliases
# => sales
uvx xorq catalog --path $B_CAT schema sales
# => shows columns and types correctly
```

**Key point:** the git clone on the same machine works (step 6) because the absolute path in `expr.yaml` still resolves. The bug surfaces when the build directory is deleted (step 7) or the catalog is on a genuinely different machine. The data IS in the catalog zip — it just can't be found at load time.

### The workaround: use `ibis.memtable()` instead

```bash
# Same CSV, different build script
cat > $MAC_A/import_portable.py << 'PYEOF'
import pyarrow.csv as pcsv
import xorq.vendor.ibis as ibis
table = pcsv.read_csv("/tmp/xorq-repro/machine-a/sales.csv")
expr = ibis.memtable(table)
PYEOF

export BUILD_PATH=$(uvx xorq build $MAC_A/import_portable.py -e expr --builds-dir $A_BUILDS 2>/dev/null | tail -1)
uvx xorq catalog --path $A_CAT add "$BUILD_PATH" --alias sales_portable
rm -rf $A_BUILDS

# Works on machine-a after deleting builds
uvx xorq catalog --path $A_CAT run sales_portable --limit 3 -f csv -o /dev/stdout
# => works

# Works on machine-b clone
git clone $A_CAT /tmp/xorq-repro/machine-b/catalog2
uvx xorq catalog --path /tmp/xorq-repro/machine-b/catalog2 run sales_portable --limit 3 -f csv -o /dev/stdout
# => works
```

## How xorq-cloud sidesteps this

The cloud MCP server's `xorq_recon_import` uses `xo.read_csv()` — it has the same absolute-path problem. But it never surfaces because the cloud worker uses a **persistent builds directory** (`/tmp/xorq_worker_builds/builds/`). The `register()` method builds there, and `load_alias()` extracts back to the same path. The absolute paths always resolve because the builds never move. This only works because it's a single-machine stateful service.

## What we tried

### 1. `xo.read_csv()` — not portable

```python
expr = xo.read_csv("/path/to/sales.csv")
```

The build snapshots the CSV as a parquet file inside `database_tables/`, but `expr.yaml` records an absolute path to it:

```yaml
read_kwargs:
  - - path
    - /Users/paddy/-plugins/tests/bug-repro/machine-a/builds/2440e56cb950/database_tables/016275...parquet
```

The build warns: `UserWarning: The Read op path is using a local filesystem path, running the build may not work in other environments.`

After `catalog add`, the data is in the zip. But on `catalog run`, the expression tries to read from the absolute path — which doesn't exist if the build dir was deleted or the catalog was cloned to another machine.

```
$ uvx xorq catalog run sales --limit 3 -f csv -o /dev/stdout
Error: At least one path is required
```

### 2. Relative paths in the script — still not portable

```python
expr = xo.read_csv("sales.csv")  # relative path
```

The build resolves the relative path to absolute during `ArtifactStore.get_path()`, so the `expr.yaml` ends up with the same absolute path. No difference.

### 3. `xo.read_csv().cache()` — still not portable

```python
expr = xo.read_csv("sales.csv").cache(ParquetCache.from_kwargs())
```

The `CachedNode` wraps the `Read` but doesn't change how the source path is stored. Same absolute path in `expr.yaml`, same failure. Slightly different error message:

```
Error: Don't know how to deal with path "/Users/paddy/-plugins/tests/bug-repro/machine-a/builds/c1b5ba8d911a/database_tables/016275...parquet"
```

### 4. `ibis.memtable()` — portable (but is this the right approach?)

```python
import pyarrow.csv as pcsv
import xorq.vendor.ibis as ibis

table = pcsv.read_csv("sales.csv")
expr = ibis.memtable(table)
```

This works. The `expr.yaml` gets a relative path and a `memtables: true` flag:

```yaml
read_kwargs:
  - - path
    - memtables/016275bcb5bdfed95c4570cf9eea8962.parquet
  - - memtables
    - true
```

On load, `deferred_reads_to_memtables` (compiler.py:692) resolves the relative path against the extraction directory. Survives build deletion and git clone.

**We're not sure if this is the intended/recommended approach.** It requires knowing to avoid `xo.read_csv()` entirely and instead going through pyarrow + `ibis.memtable()`, which is non-obvious.

## Summary

| Approach | portable? | error on clone |
|---|---|---|
| `xo.read_csv("/abs/path")` | no | `At least one path is required` |
| `xo.read_csv("relative")` | no | same (build resolves to absolute) |
| `xo.read_csv(...).cache(...)` | no | `Don't know how to deal with path` |
| `ibis.memtable(pyarrow_table)` | **yes** | works |

## Why this matters

The catalog's value proposition is portable, shareable data artifacts. But the most natural way to get data into the catalog (`read_csv`) produces non-portable entries. The warning message hints at the problem but doesn't point to a solution.

## What works on the clone vs what doesn't

Metadata operations work fine because they don't execute the expression:

```
$ uvx xorq catalog --path machine-b/catalog list-aliases    # works
$ uvx xorq catalog --path machine-b/catalog schema sales    # works
$ uvx xorq catalog --path machine-b/catalog run sales       # fails
```

## Technical details

### Why `memtable` works and `read_csv` doesn't

During build, `InMemoryTable` nodes go through `_prepare_memtable` (compiler.py:446) which stores the parquet with a relative path (`memtables/hash.parquet`) and adds a `memtables: true` flag to `read_kwargs`.

`read_csv` produces a `Read` op. The build snapshots the data into `database_tables/hash.parquet`, but the path written to `expr.yaml` is absolute because `ArtifactStore.get_path()` returns `root_path / *parts` where `root_path` is always absolute.

During load, `deferred_reads_to_memtables` (compiler.py:687) only processes reads with the `memtables` flag — it resolves paths relative to the build directory via `expr_path.joinpath(path)`. `Read` ops without the flag go through `_transform_deferred_reads` which uses the path as-is.

### Note on `cp -r` vs `git clone`

Catalog aliases are symlinks (`aliases/sales.zip -> ../entries/hash.zip`). Copying with `cp -r` resolves symlinks into regular files, which breaks catalog consistency checks. Always use `git clone` to replicate a catalog.

## Questions for the xorq team

1. Is `ibis.memtable()` the recommended way to create portable catalog entries from local files?
2. Should `xo.read_csv()` produce portable entries by default (relative paths + memtables flag)?
3. Is there a different API we should be using for this use case?
