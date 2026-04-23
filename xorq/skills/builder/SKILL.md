---
description: Create ExprBuilder catalog entries — ML pipelines (FittedPipeline), semantic models (BSL), or custom TagHandlers. Use when the user wants to fit a model, create a semantic layer entry, or register a custom builder for round-trip recovery.
---

# Builder — Create ExprBuilder Entries

Guide the user through creating catalog entries with recoverable domain objects via the TagHandler registry. This covers ML pipelines, semantic models (BSL), and custom builders.

## ML Pipelines (FittedPipeline)

### Workflow

#### 1. Write a training script

Create a Python script that fits a pipeline and produces a tagged expression:

```python
import xorq.api as xo
from xorq.expr.ml.pipeline_lib import Pipeline
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

# Load training data
train = xo.read_csv("train.csv")

# Create and fit pipeline
sk_pipe = make_pipeline(StandardScaler(), LogisticRegression())
pipeline = Pipeline.from_instance(sk_pipe)
fitted = pipeline.fit(train, features=["feature1", "feature2"], target="label")

# Produce tagged prediction expression
expr = fitted.predict(train)
```

**Key points:**
- `Pipeline.fit()` is **deferred** — it builds an expression graph, it does not execute sklearn immediately
- `.predict()` tags the expression with `FittedPipelineTagKey.PREDICT`
- Other response methods: `.transform()`, `.predict_proba()`, `.decision_function()`, `.feature_importances()`
- The `features` parameter takes a list of column names; `target` is the label column

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

catalog = xo.catalog("<catalog-name>")
entry = catalog["<model-name>"]

# Recover the FittedPipeline domain object
fitted_pipeline = entry.expr.ls.builder

# Predict on new data
new_data = xo.read_csv("test.csv")
predictions = fitted_pipeline.predict(new_data)
```

## BSL (Boring Semantic Layer)

BSL entries wrap expressions with semantic model metadata (dimensions, measures, descriptions).

- The expression is tagged with `"bsl"` containing SemanticModel metadata
- `entry.expr.ls.builder` recovers the `SemanticTableOp` for requerying
- Catalog kind is `ExprBuilder`

## Custom TagHandlers

### Registration via Python

```python
from xorq.expr.builders import register_tag_handler, TagHandler

register_tag_handler(TagHandler(
    tag_names=("my_custom_tag",),
    extract_metadata=lambda tag_node: {
        "type": "my_custom_tag",
        # ... additional metadata
    },
    from_tag_node=lambda tag_node: recover_my_object(tag_node),
))
```

### Registration via entry point

In `pyproject.toml`:

```toml
[project.entry-points."xorq.from_tag_node"]
my_handler = "my_package.handlers:my_tag_handler"
```

### How it works

1. Tag an expression: `expr.tag({"my_custom_tag": metadata})`
2. Build and add to catalog — entry kind becomes `ExprBuilder`
3. On load: `entry.expr.ls.builder` → dispatches to registered handler's `from_tag_node()`
4. `extract_metadata()` stores handler metadata in catalog sidecar YAML (`ExprMetadata.builders`)

### Requirements

- At least one of `extract_metadata` or `from_tag_node` must be provided
- `tag_names` is a tuple of string tag names the handler responds to
- Builtin tag names (`bsl`, ML pipeline tags) cannot be overridden without `override=True`

## Tips

- ML pipeline `fit()` is deferred — the actual sklearn fitting happens at `xorq run` time, not at script execution
- The training source is structurally embedded in the expression graph — `FittedPipeline.from_tag_node()` walks the graph to find it
- `ExprMetadata.builders` stores extracted metadata so you can inspect pipeline steps without fetching the full archive
- Use `xorq run <build_path> -f json --limit 10` to preview predictions before cataloging

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a description of the builder type or model to create.
