# xorq — Ambient Context

xorq is a multi-engine data processing framework built on [Ibis](https://ibis-project.org/) and Apache DataFusion. It lets you write engine-agnostic expressions that compile to SQL across DuckDB, Postgres, Spark, Trino, and more.

## Core Concepts

### Expressions
Expressions are **lazy, immutable DAGs** — they describe computation without executing it. Every transform returns a new expression; nothing mutates in place.

```python
import xorq.api as xo
from xorq.api import _

expr = xo.deferred_read_csv("data.csv")
expr = expr.filter(_.amount > 100).select("id", "amount", "category")
```

- `xo.deferred_read_csv()` / `xo.deferred_read_parquet()` — deferred reads (preferred for build scripts)
- `xo.connect()` then `con.read_csv()` / `con.read_parquet()` — eager reads via default DuckDB backend
- Transforms: `.filter()`, `.select()`, `.mutate()`, `.group_by().agg()`, `.join()`, `.order_by()`, `.limit()`
- `xo.cases((cond, value), ..., else_=default)` — multi-branch `CASE` expressions
- `from xorq.expr.ml.pipeline_lib import Pipeline` — ML pipeline class (see ML Pipeline Pattern below)
- The `_` column selector: `from xorq.api import _` enables `_.col_name` syntax

### Builds
`xorq build script.py` compiles a Python script's `expr` variable into **content-addressed artifacts** under `builds/`. The build hash is deterministic — same expression always produces the same hash.

### Catalogs
Catalogs are **git-backed registries** of versioned expressions. Each entry is a zip archive with expression metadata stored in sidecar YAML.

- `xorq catalog init` — create a new catalog repo
- Entries are added via `xorq catalog add <path> --alias <name>`
- Aliases are human-readable symlinks to content-addressed entries
- Git-annex stores large archives; metadata always in git

**Python catalog API:**
```python
from xorq.catalog.catalog import Catalog

cat = Catalog.from_default()        # load default catalog
entry = cat.load("alias_name")      # load expression by alias — returns an ibis expression
# To join multiple entries, load into a shared connection:
con = xo.connect()
t1 = cat.load("entry1", con=con)
t2 = cat.load("entry2", con=con)
joined = t1.join(t2, "key_col")
# NOTE: cat["name"] does NOT work — use cat.load("name")
# NOTE: xo.catalog() is a MODULE, not callable — use Catalog.from_default()
```

## ExprKind Taxonomy

Every catalog entry has a `kind` determined by its outermost structural layer:

| Kind | Description | How it's created |
|------|-------------|-----------------|
| `Source` | Bound source with data (table/memtable) | `xo.deferred_read_csv()`, `xo.deferred_read_parquet()` |
| `Expr` | Bound transformation | Source + transforms (filter, join, etc.) |
| `UnboundExpr` | Partial — contains UnboundTable, awaits input | Transform without a bound source |
| `Composed` | From catalog composition | `xorq catalog compose` |
| `ExprBuilder` | Tagged with registered builder | ML pipeline, BSL, custom TagHandler |

## TagHandler / Builder Pattern

Expressions can be **tagged** with metadata that enables domain object recovery. Tag syntax: `expr.tag("name", key=value)` — string tag name + kwargs (NOT a dict).

- `expr.ls.builder` triggers tag resolution → handler dispatch → domain object recovery
- `ExprMetadata.builders` tuple stores handler metadata in catalog sidecar YAML
- Third-party handlers register via `"xorq.from_tag_node"` entry point in pyproject.toml

For ML pipelines, BSL semantic models, and custom TagHandler workflows, see `/xorq:builder`.

## Catalog Composition

`xorq catalog compose` assembles existing entries into new composed expressions. For detailed workflows including `catalog run`, inline code, joins via build scripts, and caching, see `/xorq:composer`.

## Common Pitfalls

- **VIRTUAL_ENV mismatch**: If you see `VIRTUAL_ENV=... does not match the project environment path .venv`, use `uv run --active` for all uv/xorq commands — e.g. `uv run --active xorq build script.py`
- **pyproject.toml flat-layout error**: If `xorq catalog add` fails with `Multiple top-level packages discovered in a flat-layout`, add `[tool.setuptools]\npy-modules = []` to pyproject.toml
- **ML Pipeline import**: Use `from xorq.expr.ml.pipeline_lib import Pipeline` in build scripts. `xo.Pipeline` works in interactive Python but **fails inside `xorq build`** ([#1864](https://github.com/xorq-labs/xorq/issues/1864))
- **ML Pipeline API**: Use `Pipeline.from_instance(sk_pipe).fit(train, features=[...], target="...").predict(train)`. Do NOT use `deferred_fit_predict` — it returns a non-buildable object
- **sklearn dependency**: sklearn is NOT bundled — add `scikit-learn` to project dependencies
- **ibis import**: Use `from xorq.vendor import ibis` — NOT `import ibis` directly. Standalone ibis is not installed.
- **Reading data in build scripts**: Use `xo.deferred_read_csv()` / `xo.deferred_read_parquet()` — these work directly. `xo.read_csv()` does NOT exist.
- **compose only works with unbound_expr transforms**: You cannot compose two Source entries. To join sources, write a build script that loads both via `cat.load("name", con=xo.connect())` into a shared connection
- **`--no-sync` is only for `catalog add`**: Do NOT use `--no-sync` with `catalog compose` — it doesn't support that flag
- **Custom TagHandler per-process**: `register_tag_handler()` must be called in every Python process that needs it (including build scripts)
- **Custom TagHandler hashability**: Tag metadata values (kwargs to `.tag()`) must be hashable — use `tuple` not `list`, because they're stored in `FrozenOrderedDict`
- **BSL catalog recovery**: `entry.expr.ls.builder` may fail on catalog-loaded BSL entries due to CatalogSource wrapping — walk the expression graph to find the inner BSL tag node

## CLI Quick Reference

### Build & Run
```bash
xorq build <script.py> [-e expr_name] [--builds-dir builds] [--debug]
xorq run <build_path> [-f json|csv|parquet|arrow] [--limit N] [-p key=value]
xorq run-cached <build_path> [-f format] [--cache-type modification-time|snapshot] [--ttl N]
```

### Catalog
```bash
xorq catalog init [--remote-url URL] [--env-file .env]
xorq catalog info
xorq catalog list [--kind]
xorq catalog schema <name> [--json]
xorq catalog add <path>... [-a alias] [--sync|--no-sync]
xorq catalog compose <entries>... [-c "code"] [-a alias] [--dry-run] [--rename-params e,old,new]
xorq catalog run <entries>... [-c "code"] [-f format] [--limit N] [--fuse|--no-fuse]
xorq catalog remove <name>
xorq catalog log [--json]
xorq catalog check
xorq catalog sync
```
