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

**Build directory convention:** Use `--builds-dir` to isolate builds by kind and prevent hash collisions (see Common Pitfalls → "Build hash collision"):

| Kind | `--builds-dir` | Example |
|------|----------------|---------|
| Source ingestion | `builds_source` | `xorq build ingest.py --builds-dir builds_source` |
| ML pipeline | `builds_ml` | `xorq build train.py --builds-dir builds_ml` |
| BSL semantic model | `builds_bsl` | `xorq build bsl.py --builds-dir builds_bsl` |
| Custom TagHandler | `builds_custom` | `xorq build tagged.py --builds-dir builds_custom` |
| General scripts | `builds` (default) | `xorq build script.py` |

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

## Catalog Resolution (do this FIRST in every skill)

Before any `xorq catalog …` or `Catalog.from_*` call, resolve which catalog to use. **Never silently use `default`** — be explicit, and prefer a repo-scoped catalog.

### Procedure

1. **Glob the repo for an existing catalog.** From the repo root, search for `catalog.yaml` (the catalog spec file). Use the `Glob` tool, then exclude the usual ignore paths:

   ```
   pattern: **/catalog.yaml
   ```

   Discard hits inside `venv/`, `.venv/`, `node_modules/`, `.git/`, `__pycache__/`, `builds/`, or any nested catalog's own `entries/` / `aliases/`. The parent directory of a surviving hit is a candidate catalog (e.g. a hit at `./local_catalog/catalog.yaml` → catalog path `./local_catalog`).

2. **Decide based on what was found.**
   - **Exactly one candidate** → use it. Record the path; call it `CATALOG_PATH`.
   - **More than one** → list them to the user and ask which one (`AskUserQuestion`).
   - **None** → propose creating one. Compute `name="$(basename "$PWD")-catalog"` and ask the user via `AskUserQuestion`:
     > "No xorq catalog found in this repo. Create `<name>` as a repo-local catalog?" — options: **Yes** / **No, use the system default**.

     - If **Yes**: init a fresh repo-local catalog at `./<name>/`:
       ```python
       import xorq.api as xo
       xo.catalog.Catalog.from_repo_path("./<name>", init=True)
       ```
       Use `CATALOG_PATH=./<name>`.
     - If **No**: skip the path; fall back to the system default (`Catalog.from_default()`, no env override). Tell the user "using the system default catalog".

3. **Scope every CLI call to the resolved catalog.** `xorq catalog` accepts **global** `-n <name>` / `-p <path>` flags that go BEFORE the subcommand (mutually exclusive):

   ```bash
   xorq catalog -p ./<name> list --kind          # repo-local catalog at ./<name>/
   xorq catalog -p ./<name> compose source -c "…" --dry-run
   xorq catalog -p ./<name> add builds/<hash> -a <alias>

   xorq catalog -n <name> list --kind            # named catalog under ~/.local/share/xorq/catalogs/<name>
   ```

   Threading `-p` / `-n` on every call is preferred over `export XORQ_DEFAULT_CATALOG=…` because it's explicit and survives subshells. For the Python API, use `Catalog.from_repo_path(CATALOG_PATH)` for repo-local or `Catalog.from_name(NAME)` for named.

   Optional: `xorq catalog default <name>` persists a named catalog as the active default across sessions. Use this only when the user explicitly wants a sticky default; otherwise prefer explicit `-p`/`-n` on each call.

4. **Surface the decision to the user.** Once resolved, state plainly: `Using catalog: <name> at <path>` (or `Using system default catalog`). Don't keep re-asking on subsequent steps in the same session.

### Notes
- Re-run the resolution at the start of each new skill invocation, but cache the decision for the rest of the session.
- The `<root>-catalog` naming convention keeps repo and catalog name aligned so `xorq catalog info` is self-describing.
- If the user passes a `-n <name>` or `-p <path>` argument to the skill, skip the procedure and use what they provided.

## Skill Decision Tree

Route the user to the right skill on first contact. Each skill also carries its own "When to use / When NOT to use" block that cross-references siblings.

| User intent | Skill |
|---|---|
| What's in the catalog? Inspect entries / schemas | `/xorq:catalog-explore` |
| Onboard raw `.csv` / `.parquet` files | `/xorq:init` |
| Run an existing entry, dry-run preview, compose+catalog, build a script | `/xorq:composer` |
| Query an existing BSL `ExprBuilder` (one-shot, exploratory) | `/xorq:composer` → BSL section (`compose --dry-run` with `source.ls.builder.query(...).to_tagged()`) |
| Create a new BSL / ML pipeline / custom-tag entry | `/xorq:builder` |
| Join two `Source` entries | `/xorq:composer` → build-script subsection |

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
- **Build hash collision**: A BSL, ML, or custom-tag expression built from the same source data will produce the **same build hash** as the source's own build, because the underlying expression is identical. This silently overwrites `builds/<hash>/` and corrupts whichever entry was added first. **Always use `--builds-dir` per kind** (see the Builds section table) — e.g. `--builds-dir builds_source` for ingestion, `--builds-dir builds_ml` for ML. This is the single most common failure mode in multi-step workflows.
- **ML Pipeline import**: Use `from xorq.expr.ml.pipeline_lib import Pipeline` in build scripts. `xo.Pipeline` works in interactive Python but **fails inside `xorq build`** ([#1864](https://github.com/xorq-labs/xorq/issues/1864))
- **ML Pipeline API**: Use `Pipeline.from_instance(sk_pipe).fit(train, features=[...], target="...").predict(train)`. Do NOT use `deferred_fit_predict` — it returns a non-buildable object
- **sklearn dependency**: sklearn is NOT bundled — add `scikit-learn` to project dependencies
- **ibis import**: Use `from xorq.vendor import ibis` — NOT `import ibis` directly. Standalone ibis is not installed.
- **Reading data in build scripts**: Use `xo.deferred_read_csv()` / `xo.deferred_read_parquet()` — these work directly. `xo.read_csv()` does NOT exist.
- **compose only works with unbound_expr transforms**: You cannot compose two Source entries. To join sources, write a build script that loads both via `cat.load("name", con=xo.connect())` into a shared connection
- **`--no-sync` is only for `catalog add`**: Do NOT use `--no-sync` with `catalog compose` — it doesn't support that flag
- **Custom TagHandler per-process**: `register_tag_handler()` must be called in every Python process that needs it (including build scripts)
- **Custom TagHandler hashability**: Tag metadata values (kwargs to `.tag()`) must be hashable — use `tuple` not `list`, because they're stored in `FrozenOrderedDict`
- **BSL catalog recovery**: `expr.ls.builder` recovers the `SemanticModel` directly from a catalog-loaded BSL entry — call `.query(...).to_tagged()` (or just `.to_tagged()`) on it. Always end with `.to_tagged()`; a bare `.query()` returns a `SemanticAggregate` which is NOT buildable by `xorq build`
- **Do NOT parallelize `xorq catalog add`**: the catalog's underlying git / git-annex operations are not concurrency-safe. Multiple simultaneous adds will cancel each other (observed in iterate.sh log O1). Run adds sequentially — one at a time — and chain with `&&` if needed. This applies equally to `init`, `composer`, and `builder` skill flows.

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
