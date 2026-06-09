---
description: Fit, version, and run ML models on catalogued data — wrap an sklearn pipeline with Pipeline.from_instance, fit it over a catalogued expression to get a FittedPipeline, then predict / transform / predict_proba / score, and catalog the result as an expr_builder that round-trips via .ls.builder. Owns the model workflow; the generic tag/round-trip machinery lives in the builder skill.
---

# ML — Fit, Version, and Run Models on Catalogued Data

xorq's ML workflow (`xorq.expr.ml`) is one shape: **wrap an sklearn pipeline → `.fit()` over an
expression → a `FittedPipeline` → `predict` / `transform` / `score` → catalog.** A fitted pipeline's
`predict`/`transform` output is an **already builder-tagged** expr, so cataloguing its build mints an
**`expr_builder`** entry that round-trips: recover the live `FittedPipeline` with **`expr.ls.builder`**
and run it on new data.

**Boundary.** This skill owns the **model** workflow (fit / predict / transform / score / version). A
`FittedPipeline` is one instance of an **ExprBuilder** — the generic tag and round-trip machinery
(recovery via `.ls.builder`, authoring custom handlers) is the **`builder`** skill; read-only
inspection is **`catalog-explore`**.


## A. Fit a pipeline and catalog it (BUILD-ADD)

Write a fit script binding the response-method expr to `expr`, then **BUILD-ADD** it:

```python
# fit_pipeline.py — bind the result expr to `expr`
import xorq.api as xo
from xorq.expr.ml import Pipeline                 # also: xo.Pipeline / xo.Step / xo.FittedPipeline
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

source = xo.deferred_read_parquet("/abs/path/to/<train>.parquet")  # or a catalogued source's expr
sk     = SklearnPipeline([("scaler", StandardScaler()),
                          ("clf", LogisticRegression(random_state=0))])   # explicit (name, estimator) steps
fitted = Pipeline.from_instance(sk).fit(source, features=("<feat>", "<feat>"), target="<target>")
expr   = fitted.predict(source)                  # already builder-tagged -> kind: expr_builder
```

```bash
xorq build fit_pipeline.py --builds-dir builds_ml --emit-build-path-to bp.txt
xorq catalog add "$(cat bp.txt)" -a <alias>      # kind: expr_builder
```

- Bind **`expr = fitted.predict(...)`** (or `.transform(...)`) — that tagged expr is what makes the
  entry an `expr_builder`. Explicit `(name, estimator)` steps keep the captured metadata and the
  content hash stable across builds.
- The build environment must have **sklearn** (and any estimator's package) — `catalog add` snapshots
  it into the entry.

## Construct and fit

```python
pipe = Pipeline.from_instance(SklearnPipeline([("scaler", StandardScaler()),
                                               ("clf", LogisticRegression(random_state=0))]))
pipe = pipe.set_params(clf__C=0.1)               # sklearn double-underscore reparameterization
```

- `Pipeline.from_instance(sklearn_pipeline)` wraps each `(name, estimator)` slot as a xorq `Step`. Lone
  estimator: `Step.from_instance_name(est, "<name>")`, then `Pipeline((step, …))`. A `ColumnTransformer`
  is a normal first step; `pipe.remap_columns({...})` / `remap_params({...})` retarget it for a
  differently-named source.
- **`fit(source, features=…, target=…, cache=…)`.** `features` defaults to every non-`target` column —
  pass it when the source carries ids/extras. **`target` is required** for a predict step (except
  clustering), else `Can't infer target for a prediction step`.
- **Split deterministically** (hash-based, from `xorq.expr.ml`):
  `train, test = source.pipe(train_test_splits, test_sizes=[0.5, 0.5], num_buckets=2, random_seed=42)`.
- **`cache=`** (e.g. `from xorq.caching import ParquetCache; cache = ParquetCache()`) memoizes the fit so
  recovery hits cache instead of refitting.
- Single-estimator shortcut: `deferred_fit_predict_sklearn` / `deferred_fit_transform` fit+apply one
  estimator inline without a `Pipeline` — handy, but the object API above is the round-trippable path.

## Predict, transform, score

Each response method returns an **already builder-tagged** expr (any one can be the `expr` you build):

| Method | Returns |
|---|---|
| `fitted.predict(test, name="<predcol>")` | prediction column (name it with `name=`) |
| `fitted.transform(test)` | preprocessed feature columns |
| `fitted.predict_proba(test)` | class probabilities (classifiers) |
| `fitted.decision_function(test)` | decision scores |
| `fitted.feature_importances(test)` | per-feature importances |

Score with **deferred** execution — nothing runs until `.execute()`:

```python
acc = fitted.score_expr(test, scorer="accuracy").execute()   # scorer: sklearn name | callable | None
# also: deferred_sklearn_metric(expr=fitted.predict(test), target="<target>", pred="<predcol>", metric=<fn>),
#       deferred_cross_val_score(pipe, source, features=("<feat>",), target="<target>", cv=5)
```

`fitted.score(X, y, scorer=...)` is the eager, sklearn-style variant over in-memory arrays.

## B. Recover and run from the catalog (RECOVER)

The entry round-trips: **RECOVER** recovers the live `FittedPipeline` and runs its own methods on new
data:

```bash
xorq catalog run <alias> \
  -c 'source.ls.builder.predict(xo.deferred_read_parquet("/abs/path/to/<new>.parquet"))' \
  -o - -f json --limit 5
```

`source.ls.builder` → the `FittedPipeline`; then `.predict(...)` / `.transform(...)` / `.predict_proba(...)`.
This is **`builder`** §A applied to a fitted pipeline — see it for the general pattern.

## Verify

Run **VERIFY**. Expect:

```bash
xorq catalog list --kind        # expect: <hash>  expr_builder
xorq catalog show <alias>       # "Type: Expression Builder", "Builders:" (type: fitted_pipeline, steps, target)
```

## Pitfalls (ml-specific; shared ones are in the kernel)

- **`predict`/`transform` are already tagged → use BUILD-ADD, NOT `catalog compose`.** Composing wraps
  the tagged expr as a **`composed`** entry (the model stays recoverable via `.ls.builder`); to mint an
  **`expr_builder`**, the response-method expr must be the **outermost** tag, i.e. the `expr` you build.
- **Recovery reconstructs from the embedded training source.** `.ls.builder` re-derives the
  `FittedPipeline` from the training expression captured in the entry — so (a) fit over a **resolvable**
  source (a `deferred_read_*` of a stable absolute path, or a catalogued source), not a transient
  `memtable`; and (b) **seed estimators** (`random_state=`) and pass `cache=` so recovery is reproducible.
- **`target` required for a predict step** (except clustering); **`features` defaults to non-target
  columns** — pass `features=(…)` when the source carries ids/extras.
- **Build-env deps:** sklearn and every estimator's package must be importable when building (or
  `--use-this-venv` when the active env already has them).

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the data + target to model (and any features
/ estimator), and/or the target catalog (`-p` / `-n`).
