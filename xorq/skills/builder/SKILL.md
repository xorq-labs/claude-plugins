---
description: Create ExprBuilder catalog entries — ML pipelines (FittedPipeline), semantic models (BSL), or custom TagHandlers. Use when the user wants to fit a model, create a semantic layer entry, or register a custom builder for round-trip recovery.
---

# Builder — Create ExprBuilder Entries

Guide the user through creating catalog entries with recoverable domain objects via the TagHandler registry. This covers ML pipelines, semantic models (BSL), and custom builders.

## When to use / When NOT to use

- **Use when**: creating an `ExprBuilder` entry — ML pipeline (`FittedPipeline`), BSL semantic model, or custom `TagHandler`.
- **Not when**:
  - Querying an existing BSL entry → `/xorq:composer` BSL section (much faster — no build script needed; one `compose --dry-run` with inline `source.ls.builder.query(...).to_tagged()`).
  - Onboarding raw `.csv` / `.parquet` files → `/xorq:init`.
  - Composing two `Source` entries → `/xorq:composer` build-script subsection.

## Step 0: Resolve target catalog

Before any `xorq catalog …` or `Catalog.from_*` call, run the **Catalog Resolution** procedure from `xorq/CLAUDE.md` — glob for an existing `catalog.yaml`, ask the user about creating `<repo-name>-catalog` if none is found, or fall back to the system default. In build/recovery scripts, use `Catalog.from_repo_path("<resolved-path>")` instead of `Catalog.from_default()` when working with a repo-local catalog.

## ML Pipelines (FittedPipeline)

Use this section for: fitting an sklearn `Pipeline` against a xorq expression and storing the fitted model as an `ExprBuilder` catalog entry that can be recovered and applied to new data.

### Prerequisites

**sklearn must be installed.** If not already in dependencies, add it:

```bash
uv add scikit-learn
# or add "scikit-learn" to pyproject.toml dependencies
```

### Workflow

#### 1. Write a training script

Create a Python script that fits a pipeline and produces a tagged expression:

```python
import xorq.api as xo
from xorq.expr.ml.pipeline_lib import Pipeline
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

# Load training data — use ABSOLUTE paths
train = xo.deferred_read_csv("/absolute/path/to/train.csv")

# Create and fit pipeline
sk_pipe = make_pipeline(StandardScaler(), LogisticRegression())
pipeline = Pipeline.from_instance(sk_pipe)
fitted = pipeline.fit(train, features=["feature1", "feature2"], target="label")

# Produce tagged prediction expression
expr = fitted.predict(train)
```

**IMPORTANT — correct imports (see CLAUDE.md Common Pitfalls for full list):**
- `from xorq.expr.ml.pipeline_lib import Pipeline` — this is the correct import for build scripts
- Do NOT use `xo.Pipeline` in build scripts — see CLAUDE.md "ML Pipeline import" pitfall
- Do NOT use `deferred_fit_predict` — see CLAUDE.md "ML Pipeline API" pitfall

**Key points:**
- `Pipeline.fit()` is **deferred** — it builds an expression graph, it does not execute sklearn immediately
- `.predict()` tags the expression with `FittedPipelineTagKey.PREDICT`
- Other response methods: `.transform()`, `.predict_proba()`, `.decision_function()`, `.feature_importances()`
- The `features` parameter takes a list of column names; `target` is the label column
- Features must be **numeric columns** — filter out non-numeric columns before fitting

#### 2. Build and catalog

```bash
xorq build <script.py> --builds-dir builds_ml
xorq catalog add builds_ml/<hash> --alias <model-name>
```

`--builds-dir builds_ml` is **required** — without it, the build hash will collide with the source's build (see CLAUDE.md "Build hash collision" pitfall).

#### 4. Verify

Check the entry kind — it should be `expr_builder`:

```bash
xorq catalog list --kind
```

Inspect the schema:

```bash
xorq catalog schema <model-name> --json
```

#### 5. Round-trip test (optional)

Write a script that loads from catalog, recovers the pipeline, and predicts on new data:

```python
import xorq.api as xo
from xorq.catalog.catalog import Catalog

cat = Catalog.from_default()
ml_expr = cat.load("<model-name>")  # NOT catalog["name"] — use .load()

# Recover the FittedPipeline domain object
fitted_pipeline = ml_expr.ls.builder

# Predict on new data
new_data = xo.deferred_read_csv("/absolute/path/to/test.csv")
predictions = fitted_pipeline.predict(new_data)
expr = predictions  # this is what xorq build captures
```

```bash
xorq build predict_script.py --builds-dir builds_ml
xorq catalog add builds_ml/<hash> --alias <predictions-name>
```

**IMPORTANT API notes** (see CLAUDE.md Common Pitfalls for details):
- Use `Catalog.from_default()` — `xo.catalog()` is a module, not callable
- Use `cat.load("alias")` — NOT `cat["alias"]`
- Use `xo.deferred_read_csv()` / `xo.deferred_read_parquet()` in build scripts

## BSL (Boring Semantic Layer)

Use this section for: **creating** a new BSL `ExprBuilder` entry (defining dimensions/measures over a source). For **querying** an existing BSL entry, use `/xorq:composer` BSL section instead — it's a one-shot `compose --dry-run` with no build script.

BSL entries wrap expressions with semantic model metadata (dimensions, measures, descriptions).

### Creating a BSL expression

**API (verified):** `SemanticModel(table=expr, dimensions={name: Dimension(expr=lambda t: t.col)}, measures={name: Measure(expr=lambda t: t.col.sum())})`

```python
import xorq.api as xo
from boring_semantic_layer import SemanticModel, Dimension, Measure

source = xo.deferred_read_csv("/absolute/path/to/data.csv")

# Dimensions and measures are DICTS (not lists), values use lambda expr
model = SemanticModel(
    table=source,
    name="my_model",
    dimensions={
        "category": Dimension(expr=lambda t: t.category),
        "region": Dimension(expr=lambda t: t.region),
    },
    measures={
        "total_amount": Measure(expr=lambda t: t.amount.sum()),
        "avg_amount": Measure(expr=lambda t: t.amount.mean()),
    },
)

# to_tagged() produces the buildable expression for xorq build
expr = model.to_tagged()

# Or query first then use to_tagged on the model:
# queried = model.query(dimensions=["category"], measures=["total_amount"])
```

Build and catalog:

```bash
xorq build create_bsl.py --builds-dir builds_bsl
xorq catalog add builds_bsl/<hash> --alias <bsl-name>
```

`--builds-dir builds_bsl` is **required** — the BSL expression wraps the same source, so without it the build hash collides with the source's build.

### Querying a BSL entry — default to `compose --dry-run`

Once a BSL entry is in the catalog (kind `ExprBuilder`), the **preferred way to query it** is to compose it with inline `-c` code that calls `.ls.builder.query(...).to_tagged()`. Always start with `--dry-run` to preview the resulting schema before materialising anything:

```bash
xorq catalog -p <catalog-path> compose <bsl-source-entry> \
  -c 'source.ls.builder.query(
        dimensions=["dim1","dim2"],
        measures=["measure1","measure2"]
      ).to_tagged()' \
  --dry-run
```

The dry-run prints the composition plan (entries, code, resulting schema). If it looks right:

- **To execute and view results** (no catalog write): swap `compose --dry-run` for `run -f json --limit 20` and drop the `-a` flag.
- **To catalog the resulting expression**: drop `--dry-run` and add `-a <alias>`:
  ```bash
  xorq catalog -p <catalog-path> compose <bsl-source-entry> \
    -c 'source.ls.builder.query(dimensions=[...], measures=[...]).to_tagged()' \
    -a <alias>
  ```

Chain a transform entry into the same `compose` if you want to layer further on top:

```bash
xorq catalog -p <catalog-path> compose <bsl-source-entry> <transform-entry> \
  -c '…to_tagged()' --dry-run
```

`.to_tagged()` is required at the end — `.query()` alone returns a `SemanticAggregate` which is NOT buildable by `xorq build` / `compose`.

### Recovery from catalog (Python — for build scripts)

`expr.ls.builder` recovers the `SemanticModel` directly from a catalog-loaded BSL entry. No tag-walking, no `from_tagged()` workaround:

```python
import xorq.api as xo
from xorq.catalog.catalog import Catalog

cat = Catalog.from_repo_path("<catalog-path>")  # or Catalog.from_default()
loaded_expr = cat.load("my_bsl_entry")

recovered_model = loaded_expr.ls.builder      # SemanticModel

# Buildable: tag the model directly, or query then tag
expr = recovered_model.to_tagged()
# OR with a specific query:
# expr = recovered_model.query(dimensions=["region"], measures=["avg_amount"]).to_tagged()
```

**Always end with `.to_tagged()`** — bare `.query()` returns a `SemanticAggregate` which is NOT buildable by `xorq build`. For one-shot composition, prefer the inline `compose --dry-run` flow above; drop into a build script only when you need multi-step Python logic.

- Catalog kind for BSL entries is `ExprBuilder`
- The stored expression carries a `"bsl"` tag whose metadata is the serialised SemanticModel

## Custom TagHandlers

Use this section for: defining a domain object that recovers from arbitrary metadata stored on an expression tag (anything that isn't an ML `FittedPipeline` or a BSL `SemanticModel`).

**Always** use `--builds-dir builds_custom` when building tagged scripts (required to avoid hash collisions — see CLAUDE.md "Build hash collision" pitfall).

### Registration via Python

**CRITICAL:** The TagHandler must be registered in EVERY Python process that needs it (see CLAUDE.md "Custom TagHandler per-process" pitfall). Since `xorq build` spawns a subprocess, you must register the handler **inside the build script itself** (not in a separate setup step).

**`.tag()` API:** `expr.tag("tag_name", key=value, key2=value2)` — string tag name + keyword args. NOT a dict.

```python
import xorq.api as xo
from xorq.expr.builders import register_tag_handler, TagHandler

# Register — MUST happen before .ls.builder is called
register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tag_node: {"type": "my_custom_tag", "column": tag_node.metadata.get("column", "")},
    from_tag_node=lambda tag_node: dict(tag_node.metadata),
))

# Tag an expression — use string tag name + kwargs (NOT a dict!)
source = xo.deferred_read_csv("/absolute/path/to/data.csv")
expr = source.tag("my_custom_tag", column="age", threshold=0.5)
# tag_node.metadata will be {"tag": "my_custom_tag", "column": "age", "threshold": 0.5}
```

### Tag metadata must reflect the data

If your tag stores parameters derived from the source (means, stds, thresholds, bucket edges), **compute them from the source expression before tagging** — do not embed magic numbers in both the transform and the `.tag()` call. The whole point of the handler's `from_tag_node` recovery is that the metadata matches the transform.

One-line pattern:

```python
mean = source.col.mean().execute()
std  = source.col.std().execute()
normalized = source.mutate(col_norm=(source.col - mean) / std)
expr = normalized.tag("normalization_config", column="col", mean=mean, std=std)
```

The values bind once, flow into both the `mutate` and the `tag`, and round-trip cleanly via `recovered.from_tag_node()`.

### Complete round-trip example (build + recover)

**Script 1: Create and catalog the tagged expression**
```python
import xorq.api as xo
from xorq.expr.builders import register_tag_handler, TagHandler

register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tn: {"type": "my_custom_tag", "column": tn.metadata.get("column", "")},
    from_tag_node=lambda tn: dict(tn.metadata),
))

source = xo.deferred_read_csv("/path/to/data.csv")
expr = source.tag("my_custom_tag", column="age", transform="filter_positive")
```

```bash
xorq build create_tagged.py --builds-dir builds_custom
xorq catalog add builds_custom/<hash> --alias my_tagged_entry
```

**Script 2: Recover from catalog and create new expression**
```python
import xorq.api as xo
from xorq.expr.builders import register_tag_handler, TagHandler
from xorq.catalog.catalog import Catalog

# MUST re-register handler in this process too
register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tn: {"type": "my_custom_tag", "column": tn.metadata.get("column", "")},
    from_tag_node=lambda tn: dict(tn.metadata),
))

cat = Catalog.from_default()
loaded_expr = cat.load("my_tagged_entry")
builder = loaded_expr.ls.builder  # returns dict from from_tag_node

# Use recovered metadata to create a new expression
new_data = xo.deferred_read_csv("/path/to/new_data.csv")
expr = new_data.tag("my_custom_tag", **{k: v for k, v in builder.items() if k != "tag"}, derived=True)
```

```bash
xorq build recover_tagged.py --builds-dir builds_custom
xorq catalog add builds_custom/<hash> --alias my_derived_entry
```

### Registration via entry point (persistent across processes)

In `pyproject.toml`:

```toml
[project.entry-points."xorq.from_tag_node"]
my_handler = "my_package.handlers:my_tag_handler"
```

This avoids needing to re-register in every script.

### How it works

1. Tag an expression: `expr.tag("my_custom_tag", key=value, ...)` — string name + kwargs (verified signature: `Table.tag(self, tag, **kwargs)`)
2. Build and add to catalog — entry kind becomes `ExprBuilder`
3. On load: `entry.expr.ls.builder` → dispatches to registered handler's `from_tag_node()`
4. `extract_metadata()` stores handler metadata in catalog sidecar YAML (`ExprMetadata.builders`)

### Requirements

- At least one of `extract_metadata` or `from_tag_node` must be provided
- `tag_names` is a tuple of string tag names the handler responds to
- Builtin tag names (`bsl`, ML pipeline tags) cannot be overridden without `override=True`
- Tag metadata values (kwargs to `.tag()`) must be hashable — see CLAUDE.md "Custom TagHandler hashability" pitfall

## Tips

- ML pipeline `fit()` is deferred — the actual sklearn fitting happens at `xorq run` time, not at script execution
- The training source is structurally embedded in the expression graph — `FittedPipeline.from_tag_node()` walks the graph to find it
- `ExprMetadata.builders` stores extracted metadata so you can inspect pipeline steps without fetching the full archive
- Use `xorq run <build_path> -f json --limit 10` to preview predictions before cataloging

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a description of the builder type or model to create.
