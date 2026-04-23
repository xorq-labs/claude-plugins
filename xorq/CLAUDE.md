# xorq — Ambient Context

xorq is a multi-engine data processing framework built on [Ibis](https://ibis-project.org/) and Apache DataFusion. It lets you write engine-agnostic expressions that compile to SQL across DuckDB, Postgres, Spark, Trino, and more.

## Core Concepts

### Expressions
Expressions are **lazy, immutable DAGs** — they describe computation without executing it. Every transform returns a new expression; nothing mutates in place.

```python
import xorq.api as xo
from xorq.api import _

expr = xo.read_csv("data.csv")
expr = expr.filter(_.amount > 100).select("id", "amount", "category")
```

- `xo.read_csv()` / `xo.read_parquet()` — lazy reads via default DuckDB backend
- `xo.deferred_read_csv()` / `xo.deferred_read_parquet()` — for non-default backends
- `xo.connect()` — explicit backend connection
- Transforms: `.filter()`, `.select()`, `.mutate()`, `.group_by().agg()`, `.join()`, `.order_by()`, `.limit()`
- The `_` column selector: `from xorq.api import _` enables `_.col_name` syntax

### Builds
`xorq build script.py` compiles a Python script's `expr` variable into **content-addressed artifacts** under `builds/`. The build hash is deterministic — same expression always produces the same hash.

### Catalogs
Catalogs are **git-backed registries** of versioned expressions. Each entry is a zip archive with expression metadata stored in sidecar YAML.

- `xorq catalog init` — create a new catalog repo
- Entries are added via `xorq catalog add <path> --alias <name>`
- Aliases are human-readable symlinks to content-addressed entries
- Git-annex stores large archives; metadata always in git

## ExprKind Taxonomy

Every catalog entry has a `kind` determined by its outermost structural layer:

| Kind | Description | How it's created |
|------|-------------|-----------------|
| `Source` | Bound source with data (table/memtable) | `xo.read_csv()`, `xo.read_parquet()` |
| `Expr` | Bound transformation | Source + transforms (filter, join, etc.) |
| `UnboundExpr` | Partial — contains UnboundTable, awaits input | Transform without a bound source |
| `Composed` | From catalog composition | `xorq catalog compose` |
| `ExprBuilder` | Tagged with registered builder | ML pipeline, BSL, custom TagHandler |

## TagHandler / Builder Pattern

Expressions can be **tagged** with metadata that enables domain object recovery:

```python
# Tag an expression
tagged_expr = expr.tag({"my_tag": {"key": "value"}})

# Register a handler for round-trip recovery
from xorq.expr.builders import register_tag_handler, TagHandler
register_tag_handler(TagHandler(
    tag_names=("my_tag",),
    extract_metadata=lambda tag_node: {"type": "my_tag", ...},
    from_tag_node=lambda tag_node: recover_domain_object(tag_node),
))
```

- `expr.ls.builder` triggers tag resolution → handler dispatch → domain object recovery
- `ExprMetadata.builders` tuple stores handler metadata in catalog sidecar YAML
- Third-party handlers register via `"xorq.from_tag_node"` entry point in pyproject.toml

## ML Pipeline Pattern

```python
from xorq.expr.ml.pipeline_lib import Pipeline
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

sk_pipe = make_pipeline(StandardScaler(), LogisticRegression())
pipeline = Pipeline.from_instance(sk_pipe)
fitted = pipeline.fit(train_expr, features=["col1", "col2"], target="label")
expr = fitted.predict(train_expr)  # Tagged with FittedPipelineTagKey.PREDICT
```

- `Pipeline.fit()` is **deferred** — builds expression graph, doesn't execute sklearn
- `.predict()`, `.transform()`, `.predict_proba()` produce tagged expressions
- `FittedPipeline.from_tag_node()` replays fit on recovered training source
- Catalog kind is `ExprBuilder` when added

## Catalog Composition

`xorq catalog compose` assembles existing entries into new composed expressions:

- **Source entry** (`kind=Source`) — bound, has data
- **Transform entries** (`kind=UnboundExpr`) — partial, awaits input
- `-c "code"` — inline Ibis code applied to `source` variable
- `--dry-run` — preview schema without cataloging
- `--rename-params entry,old,new` — resolve parameter name collisions

## Common Pitfalls

- **pyproject.toml flat-layout error**: If `xorq catalog add` fails with `Multiple top-level packages discovered in a flat-layout`, add `[tool.setuptools]\npy-modules = []` to pyproject.toml
- **ML Pipeline import**: Use `from xorq.expr.ml.pipeline_lib import Pipeline` — NOT `xo.Pipeline`
- **ML Pipeline API**: Use `Pipeline.from_instance(sk_pipe).fit(train, features=[...], target="...").predict(train)`. Do NOT use `deferred_fit_predict` — it returns a non-buildable object
- **sklearn dependency**: sklearn is NOT bundled — add `scikit-learn` to project dependencies
- **ibis import**: Use `from xorq.vendor import ibis` — NOT `import ibis` directly. Standalone ibis is not installed.
- **compose only works with unbound_expr transforms**: You cannot compose two Source entries. To join sources, use `-c` inline code or write a build script
- **Custom TagHandler per-process**: `register_tag_handler()` must be called in every Python process that needs it (including build scripts)
- **Custom TagHandler hashability**: `extract_metadata` return values must be hashable — use `tuple` not `list`
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
