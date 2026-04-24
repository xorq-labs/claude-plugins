---
description: Create ExprBuilder catalog entries — ML pipelines (FittedPipeline), semantic models (BSL), or custom TagHandlers. Use when the user wants to fit a model, create a semantic layer entry, or register a custom builder for round-trip recovery.
---

# Builder — Create ExprBuilder Entries

Guide the user through creating catalog entries with recoverable domain objects via the TagHandler registry. This covers ML pipelines, semantic models (BSL), and custom builders.

## ML Pipelines (FittedPipeline)

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

**IMPORTANT — correct imports:**
- `from xorq.expr.ml.pipeline_lib import Pipeline` — this is the correct import for build scripts
- Do NOT use `xo.Pipeline` — it works in interactive Python but **fails inside `xorq build`** ([#1864](https://github.com/xorq-labs/xorq/issues/1864))
- Do NOT use `deferred_fit_predict` — it returns a `DeferredFitOther` which is NOT a buildable expression and causes hashing errors. Always use `Pipeline.from_instance(sk_pipe).fit(train, ...).predict(train)` pattern.

**Key points:**
- `Pipeline.fit()` is **deferred** — it builds an expression graph, it does not execute sklearn immediately
- `.predict()` tags the expression with `FittedPipelineTagKey.PREDICT`
- Other response methods: `.transform()`, `.predict_proba()`, `.decision_function()`, `.feature_importances()`
- The `features` parameter takes a list of column names; `target` is the label column
- Features must be **numeric columns** — filter out non-numeric columns before fitting

#### 2. Build the script

```bash
xorq build <script.py>
```

#### 3. Add to catalog

```bash
xorq catalog add builds/<hash> --alias <model-name>
```

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

**IMPORTANT API notes:**
- `xo.catalog()` is a MODULE, not callable — use `from xorq.catalog.catalog import Catalog; Catalog.from_default()`
- Use `cat.load("alias")` — NOT `cat["alias"]` (no subscript support)
- Use `xo.deferred_read_csv()` / `xo.deferred_read_parquet()` for reading data in build scripts

## BSL (Boring Semantic Layer)

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

### Recovery from catalog

**`from_tagged(cat.load("alias"))` WILL FAIL** because catalog wraps with HashingTag. You MUST walk the tags to find the BSL tag node:

```python
from boring_semantic_layer import from_tagged
from xorq.catalog.catalog import Catalog

cat = Catalog.from_default()
loaded_expr = cat.load("my_bsl_entry")

# Walk tags to find the BSL tag — do NOT pass loaded_expr directly to from_tagged
tags = loaded_expr.ls.get_tags()
bsl_tag = [t for t in tags if hasattr(t, "tag") and t.tag == "bsl"][0]
recovered_model = from_tagged(bsl_tag.to_expr())

# To build a new expression from the recovered model:
expr = recovered_model.to_tagged()  # this is buildable by xorq build
```

**IMPORTANT:** `.query()` returns `SemanticAggregate`, which is NOT directly buildable by `xorq build`. You MUST call `.to_tagged()` on the recovered model to get a buildable expression:

```python
expr = recovered_model.to_tagged()  # buildable — use this
# NOT: expr = recovered_model.query(...)  # SemanticAggregate — xorq build will fail
```

If you want to query with specific dimensions/measures AND make it buildable, query first then tag:

```python
queried = recovered_model.query(dimensions=["region"], measures=["avg_amount"])
# queried is SemanticAggregate — wrap it for build:
expr = queried.to_tagged() if hasattr(queried, 'to_tagged') else recovered_model.to_tagged()
```

- The expression is tagged with `"bsl"` containing SemanticModel metadata
- `entry.expr.ls.builder` recovers the `SemanticTableOp` for requerying (when not wrapped by CatalogSource)
- Catalog kind is `ExprBuilder`

## Custom TagHandlers

### Registration via Python

**CRITICAL:** The TagHandler must be registered in EVERY Python process that needs it. Since `xorq build` spawns a subprocess, you must register the handler **inside the build script itself** (not in a separate setup step).

**`.tag()` API (verified):** `expr.tag("tag_name", key=value, key2=value2)` — string tag name + keyword args. NOT a dict.

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
- **`extract_metadata` must return hashable types** — use `tuple` instead of `list` in returned dicts, because values get wrapped in `FrozenOrderedDict`

## Tips

- **Build hash collision**: A BSL or ML expression built from the same source data may produce the same build hash as the source's original build. This overwrites the `builds/<hash>/` directory. Always `catalog add` the builder entry BEFORE rebuilding the source, or use separate build directories (`--builds-dir`).
- ML pipeline `fit()` is deferred — the actual sklearn fitting happens at `xorq run` time, not at script execution
- The training source is structurally embedded in the expression graph — `FittedPipeline.from_tag_node()` walks the graph to find it
- `ExprMetadata.builders` stores extracted metadata so you can inspect pipeline steps without fetching the full archive
- Use `xorq run <build_path> -f json --limit 10` to preview predictions before cataloging

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a description of the builder type or model to create.
