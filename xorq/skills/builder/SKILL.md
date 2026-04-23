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
con = xo.connect()
train = con.read_csv("/absolute/path/to/train.csv")

# Create and fit pipeline
sk_pipe = make_pipeline(StandardScaler(), LogisticRegression())
pipeline = Pipeline.from_instance(sk_pipe)
fitted = pipeline.fit(train, features=["feature1", "feature2"], target="label")

# Produce tagged prediction expression
expr = fitted.predict(train)
```

**IMPORTANT — correct imports:**
- `from xorq.expr.ml.pipeline_lib import Pipeline` — this is the correct import
- Do NOT use `xo.Pipeline` — it does not exist on the xo.api module
- Do NOT use `from xorq.vendor import ibis` and then `ibis.Pipeline` — Pipeline is not part of ibis
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
con = xo.connect()
new_data = con.read_csv("/absolute/path/to/test.csv")
predictions = fitted_pipeline.predict(new_data)
expr = predictions  # this is what xorq build captures
```

**IMPORTANT API notes:**
- `xo.catalog()` is a MODULE, not callable — use `from xorq.catalog.catalog import Catalog; Catalog.from_default()`
- Use `cat.load("alias")` — NOT `cat["alias"]` (no subscript support)
- Use `xo.connect().read_csv()` — NOT `xo.read_csv()` (doesn't exist)

## BSL (Boring Semantic Layer)

BSL entries wrap expressions with semantic model metadata (dimensions, measures, descriptions).

### Creating a BSL expression

```python
import xorq.api as xo
from boring_semantic_layer import SemanticModel, Dimension, Measure

# Source data
con = xo.connect()
source = con.read_csv("/absolute/path/to/data.csv")

# Define semantic model
model = SemanticModel(
    name="my_model",
    table=source,
    dimensions=[
        Dimension(name="category", description="Product category"),
        Dimension(name="region", description="Sales region"),
    ],
    measures=[
        Measure(name="total_amount", expr="sum(amount)", description="Total sales"),
        Measure(name="avg_amount", expr="mean(amount)", description="Average sale"),
    ],
)

# Query the model — this produces a tagged expression
semantic_op = model.semantic_table_op()
expr = semantic_op.query(
    dimensions=["category"],
    measures=["total_amount"],
)
```

### Recovery from catalog

**IMPORTANT:** When loading BSL expressions from catalog, `entry.expr.ls.builder` may fail with `ValueError: No BSL metadata found` because the catalog wraps the expression with a `CatalogSource` tag. Use this workaround:

```python
import xorq.api as xo
from xorq.expr.relations import Tag
from xorq.catalog.catalog import Catalog

cat = Catalog.from_default()
loaded_expr = cat.load("my_bsl_entry")

# Walk the expression graph to find the BSL tag node
def find_bsl_tag(expr):
    """Walk expression graph to find the inner BSL tag node."""
    for node in expr.op().find(Tag):
        if "bsl" in (node.tag or {}):
            return node
    return None

bsl_tag = find_bsl_tag(loaded_expr)
if bsl_tag:
    from boring_semantic_layer import from_tagged
    recovered = from_tagged(bsl_tag.to_expr())
    # recovered is a SemanticAggregate — to re-query with different dimensions:
    # navigate to the base SemanticTableOp
```

- The expression is tagged with `"bsl"` containing SemanticModel metadata
- `entry.expr.ls.builder` recovers the `SemanticTableOp` for requerying (when not wrapped by CatalogSource)
- Catalog kind is `ExprBuilder`

## Custom TagHandlers

### Registration via Python

**CRITICAL:** The TagHandler must be registered in EVERY Python process that needs it. Since `xorq build` spawns a subprocess, you must register the handler **inside the build script itself** (not in a separate setup step).

```python
import xorq.api as xo
from xorq.expr.builders import register_tag_handler, TagHandler

# Define a recovery function
def recover_my_object(tag_node):
    """Recover domain object from tag node."""
    metadata = tag_node.tag.get("my_custom_tag", {})
    # ... reconstruct your domain object
    return metadata  # or a domain object

# Register — MUST happen before .ls.builder is called
register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tag_node: {
        "type": "my_custom_tag",
        # ... additional metadata from tag_node.tag["my_custom_tag"]
    },
    from_tag_node=recover_my_object,
))

# Tag an expression
con = xo.connect()
source = con.read_csv("/absolute/path/to/data.csv")
tagged_expr = source.tag({"my_custom_tag": {"key": "value", "description": "my custom metadata"}})
expr = tagged_expr  # this is what xorq build captures
```

### Complete round-trip example (build + recover)

**Script 1: Create and catalog the tagged expression**
```python
import xorq.api as xo
from xorq.expr.builders import register_tag_handler, TagHandler

# Register handler
register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tn: {"type": "my_custom_tag", **tn.tag.get("my_custom_tag", {})},
    from_tag_node=lambda tn: tn.tag.get("my_custom_tag", {}),
))

con = xo.connect()
source = con.read_csv("/path/to/data.csv")
expr = source.tag({"my_custom_tag": {"transform": "filter_positive"}})
```

**Script 2: Recover from catalog and create new expression**
```python
import xorq.api as xo
from xorq.expr.builders import register_tag_handler, TagHandler
from xorq.catalog.catalog import Catalog

# MUST re-register handler in this process too
register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tn: {"type": "my_custom_tag", **tn.tag.get("my_custom_tag", {})},
    from_tag_node=lambda tn: tn.tag.get("my_custom_tag", {}),
))

cat = Catalog.from_default()
loaded_expr = cat.load("my_tagged_entry")
builder = loaded_expr.ls.builder  # dispatches to from_tag_node

# Use recovered metadata to create a new expression
con = xo.connect()
new_data = con.read_csv("/path/to/new_data.csv")
expr = new_data.tag({"my_custom_tag": {**builder, "derived": True}})
```

### Registration via entry point (persistent across processes)

In `pyproject.toml`:

```toml
[project.entry-points."xorq.from_tag_node"]
my_handler = "my_package.handlers:my_tag_handler"
```

This avoids needing to re-register in every script.

### How it works

1. Tag an expression: `expr.tag({"my_custom_tag": metadata})`
2. Build and add to catalog — entry kind becomes `ExprBuilder`
3. On load: `entry.expr.ls.builder` → dispatches to registered handler's `from_tag_node()`
4. `extract_metadata()` stores handler metadata in catalog sidecar YAML (`ExprMetadata.builders`)

### Requirements

- At least one of `extract_metadata` or `from_tag_node` must be provided
- `tag_names` is a tuple of string tag names the handler responds to
- Builtin tag names (`bsl`, ML pipeline tags) cannot be overridden without `override=True`
- **`extract_metadata` must return hashable types** — use `tuple` instead of `list` in returned dicts, because values get wrapped in `FrozenOrderedDict`

## Tips

- ML pipeline `fit()` is deferred — the actual sklearn fitting happens at `xorq run` time, not at script execution
- The training source is structurally embedded in the expression graph — `FittedPipeline.from_tag_node()` walks the graph to find it
- `ExprMetadata.builders` stores extracted metadata so you can inspect pipeline steps without fetching the full archive
- Use `xorq run <build_path> -f json --limit 10` to preview predictions before cataloging

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a description of the builder type or model to create.
