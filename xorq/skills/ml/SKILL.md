---
description: Fit, version, and run ML models on catalogued data — wrap an sklearn pipeline with Pipeline.from_instance, fit it over a catalogued expression to get a FittedPipeline, then predict / transform / predict_proba / score, and catalog the result as an expr_builder that round-trips via .ls.builder. Owns the model workflow; the generic tag/round-trip machinery lives in the builder skill.
---

# ML — Fit, Version, and Run Models on Catalogued Data

xorq's ML workflow (`xorq.expr.ml`) is one shape: **wrap an sklearn pipeline →
`.fit()` it over an expression → get a `FittedPipeline` → `predict` / `transform` /
`score` → catalog the result.** A fitted pipeline's `predict`/`transform` output is an
**already builder-tagged** expression, so cataloguing its build mints an **`expr_builder`**
entry that round-trips: recover the live `FittedPipeline` with **`expr.ls.builder`** and run
it on new data.

```python
import xorq.api as xo
from xorq.expr.ml import Pipeline                 # also: xo.Pipeline / xo.Step / xo.FittedPipeline
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

sk     = SklearnPipeline([("scaler", StandardScaler()),
                          ("clf", LogisticRegression(random_state=0))])
pipe   = Pipeline.from_instance(sk)               # the one construction path
fitted = pipe.fit(source, features=("<feat>", "<feat>"), target="<target>")
expr   = fitted.predict(source)                   # already tagged -> kind: expr_builder
```

**Build the sklearn pipeline as explicit `(name, estimator)` steps and wrap it with
`Pipeline.from_instance(...)`.** Named steps keep the captured step metadata and the entry's
content hash stable and legible across builds.

**Boundary.** This skill owns the **model** workflow (fit / predict / transform / score /
version). A `FittedPipeline` is one instance of an **ExprBuilder** — the generic tag and
round-trip machinery (how any builder entry recovers via `.ls.builder`, authoring custom
handlers) is the **`builder`** skill; read-only inspection is **`catalog-explore`**.

## Resolve the catalog

Run the **Catalog Resolution** procedure in `xorq/CLAUDE.md` first, then thread the target on
every call (`-p <path>` or `-n <name>`) — unless a user-set default already targets it
(`xorq catalog default`), then drop the flags. Below uses `CAT=<catalog>`.

## A. Fit a pipeline and catalog it

A fitted pipeline isn't catalogued with a one-liner — write a small fit script that binds the
response-method expr to `expr`, then use the same **build → add** primitive every entry uses
(the lightest-tool ladder is ambient — see **Building expressions** in `xorq/CLAUDE.md`; the
add step mirrors **`ingest`** / **`composer`**):

```python
# fit_pipeline.py — bind the result expr to `expr`
import xorq.api as xo
from xorq.expr.ml import Pipeline
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

source = xo.deferred_read_parquet("/abs/path/to/<train>.parquet")  # or a catalogued source's expr
sk     = SklearnPipeline([("scaler", StandardScaler()),
                          ("clf", LogisticRegression(random_state=0))])
fitted = Pipeline.from_instance(sk).fit(source, features=("<feat>", "<feat>"), target="<target>")
expr   = fitted.predict(source)                  # already builder-tagged
```

```bash
CAT=<catalog>
xorq build fit_pipeline.py --builds-dir builds_ml --emit-build-path-to bp.txt
xorq catalog -p "$CAT" add "$(cat bp.txt)" -a <alias>      # kind: expr_builder
```

- Bind **`expr = fitted.predict(...)`** (or `.transform(...)`) — that tagged expr is what
  makes the entry an `expr_builder`. `xorq build` captures `expr` by default (`-e <name>` to
  override); `--builds-dir` isolates ML builds; `-a` names the entry.
- The build environment must have **sklearn** (and any estimator's package) — `catalog add`
  snapshots it into the entry (see Pitfalls).

## Construct the pipeline — named steps + `from_instance`

```python
from xorq.expr.ml import Pipeline, Step

pipe = Pipeline.from_instance(SklearnPipeline([("scaler", StandardScaler()),
                                               ("clf", LogisticRegression(random_state=0))]))
pipe = pipe.set_params(clf__C=0.1)               # sklearn double-underscore reparameterization
```

- `Pipeline.from_instance(sklearn_pipeline)` wraps each `(name, estimator)` slot as a xorq
  `Step`. For a lone estimator: `Step.from_instance_name(est, "<name>")` /
  `Step.from_name_instance("<name>", est)`, then `Pipeline((step, …))`.
- A `ColumnTransformer` preprocessor is a normal first step; `pipe.remap_columns({...})` /
  `pipe.remap_params({...})` retarget its column references or sklearn params if you reuse a
  pipeline against a differently-named source.

## Fit — features, target, splits, cache

```python
fitted = pipe.fit(source, features=("<feat>", "<feat>"), target="<target>", cache=cache)
```

- **`features`** defaults to every column except `target`; pass it explicitly when the source
  carries ids/extra columns. **`target`** is required for a predict step (except clustering) —
  omitting it raises `Can't infer target for a prediction step`.
- **Split deterministically** with `train_test_splits` (hash-based, reproducible):
  ```python
  from xorq.expr.ml import train_test_splits
  train, test = source.pipe(train_test_splits, test_sizes=[0.5, 0.5], num_buckets=2, random_seed=42)
  fitted = pipe.fit(train, features=("<feat>",), target="<target>")
  ```
- **`cache=`** memoizes the fit (e.g. `from xorq.caching import ParquetCache; cache = ParquetCache()`)
  so re-deriving the model on recovery hits cache instead of refitting.
- *Single-estimator shortcut:* `deferred_fit_predict_sklearn` / `deferred_fit_transform` (from
  `xorq.expr.ml`) fit+apply one estimator inline without a `Pipeline` object — handy for a quick
  transform, but the object API above is the canonical, round-trippable path.

## Predict, transform, and score

Each response method returns an **already builder-tagged** expr (so any one can be the `expr`
you build into an `expr_builder` entry):

```python
fitted.predict(test, name="<predcol>")    # prediction column (name it with name=)
fitted.transform(test)                     # preprocessed feature columns
fitted.predict_proba(test)                 # class probabilities (classifiers)
fitted.decision_function(test)             # decision scores
fitted.feature_importances(test)           # per-feature importances
```

Score with **deferred** execution — nothing runs until `.execute()`:

```python
acc = fitted.score_expr(test, scorer="accuracy").execute()   # scorer: sklearn name | callable | None (model default)

from xorq.expr.ml import deferred_sklearn_metric, deferred_cross_val_score
deferred_sklearn_metric(expr=fitted.predict(test), target="<target>",
                        pred="<predcol>", metric=<metric_fn>).execute()
deferred_cross_val_score(pipe, source, features=("<feat>",), target="<target>", cv=5).execute()
```

`fitted.score(X, y, scorer=...)` is the eager, sklearn-style variant over in-memory arrays.

## B. Recover and run from the catalog

The entry round-trips: `source.ls.builder` recovers the live `FittedPipeline`, and you call
its own methods on new data. A bare `run` replays the stored query; with **`-c`** you recover
and re-run on a different dataset (**`-o -` is required** — output defaults to `/dev/null`):

```bash
xorq catalog -p "$CAT" run <alias> -o - -f json --limit 5             # the stored query

xorq catalog -p "$CAT" run <alias> \
  -c 'source.ls.builder.predict(xo.deferred_read_parquet("/abs/path/to/<new>.parquet"))' \
  -o - -f json --limit 5                                              # recover + score new data
```

- `source` is the entry's expression; `source.ls.builder` → the `FittedPipeline`; then
  `.predict(...)` / `.transform(...)` / `.predict_proba(...)` — the object's own API.
- The `-c` namespace is **sandboxed** to `source` / `xo` / `ibis` — no imports or builtins.
- This is the **`builder`** skill's §A round-trip applied to a fitted pipeline; see it for the
  general pattern and **`catalog-explore`** for inspecting an entry's type and metadata.

## Verify

```bash
xorq catalog -p "$CAT" list --kind        # expect: <hash>  expr_builder
xorq catalog -p "$CAT" show <alias>       # "Type: Expression Builder", a "Builders:" block (type: fitted_pipeline, steps, target)
xorq catalog -p "$CAT" run <alias> \
  -c 'source.ls.builder.predict(xo.deferred_read_parquet("/abs/path/to/<new>.parquet"))' \
  -o - -f json --limit 5                  # round-trips: recovers the model and scores new rows
```

## Pitfalls

- **`predict`/`transform` are already tagged → use build → add, NOT `catalog compose`.**
  Composing wraps the tagged expr as a **`composed`** entry (the model stays recoverable
  underneath via `.ls.builder`); to mint an **`expr_builder`**, the response-method expr must
  be the **outermost** tag, i.e. the `expr` you `xorq build`. (Mirrors **`builder`**.)
- **Recovery reconstructs from the embedded training source.** `.ls.builder` re-derives the
  `FittedPipeline` from the training expression captured in the entry — so (a) fit over a
  **resolvable** source (a `deferred_read_*` of a stable absolute path, or a catalogued
  source), not a transient `memtable` that won't exist at recovery time; and (b) **seed your
  estimators** (`random_state=`) and pass `cache=` so the recovered model is reproducible.
- **`target` is required for a predict step** (except clustering); **`features` defaults to
  all non-target columns** — pass `features=(…)` when the source carries ids/extra columns.
- **`-c` is sandboxed** to `source` / `xo` / `ibis` — no imports, no builtins.
- **Build-env deps.** sklearn and every estimator's package must be importable in the build
  environment (or `--use-this-venv` when the active env already has them); keep
  **`requirements.txt` identical** across entries built together; do **one catalog op at a
  time** (git/annex isn't concurrency-safe); **`VIRTUAL_ENV` mismatch** → prefix with
  `uv run --active`.

## Docs

The full, machine-readable index of every CLI command and Python API is at
<https://docs.xorq.dev/llms.txt>. ML pages it indexes:
`Pipeline`, `Step`, `train_test_splits`, `deferred_fit_predict`, `deferred_fit_transform`
(reference); and the tutorials *Split data for training*, *Train your first model*, and
*Compare model performance* (`tutorials/ml_tutorials/…`).

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the data + target to model (and any
features / estimator), and/or the target catalog (`-p` / `-n`).
