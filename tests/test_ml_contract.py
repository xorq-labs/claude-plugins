"""Contract tests for the `ml` skill's documented recipes (xorq/skills/ml/SKILL.md).

Drift alarm, NOT a re-test of xorq's ML internals: it runs only the CLI + script surface the
SKILL.md tells the agent to run, so a version bump that changes these behaviors fails here and
the skill must be updated.

Pins:
  - §A fit + BUILD-ADD -> `expr = fitted.predict(...)` is already builder-tagged; the entry
                          is kind `expr_builder` and `show` reports a fitted_pipeline builder
  - §B RECOVER         -> `run -c 'source.ls.builder.predict(xo.deferred_read_csv(...))'`
                          recovers the live FittedPipeline and predicts on new data -> rows

Source data is tests/data/customers.csv (numeric `age` feature, categorical `tier` target —
fit over a resolvable absolute-path deferred read, seeded estimator, exactly as the skill
prescribes). `catalog add` builds a wheel of this project, so tests run from the repo root in
a wheel-buildable env; the round-trip uses `--use-this-venv` (sklearn is in this env — offline).
Verified against xorq 0.3.28 + scikit-learn 1.9.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("sklearn")

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "tests" / "data" / "customers.csv"

# SKILL.md §A: wrap an sklearn pipeline, fit over a deferred read, bind the predict expr.
FIT_SCRIPT = """import xorq.api as xo
from xorq.expr.ml import Pipeline
from sklearn.pipeline import Pipeline as SklearnPipeline
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression

source = xo.deferred_read_csv({csv!r})
sk = SklearnPipeline([("scaler", StandardScaler()),
                      ("clf", LogisticRegression(random_state=0))])
fitted = Pipeline.from_instance(sk).fit(source, features=("age",), target="tier")
expr = fitted.predict(source)   # already builder-tagged -> kind: expr_builder
"""


def _run(xorq_bin: str, *args: object, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(
        [xorq_bin, *map(str, args)], capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(scope="module")
def fitted_entry(xorq_bin: str, tmp_path_factory: pytest.TempPathFactory) -> tuple:
    """Build the §A fit script and add it once; return (catalog, alias)."""
    if not CSV.exists():
        pytest.skip("missing tests/data/customers.csv")
    tmp = tmp_path_factory.mktemp("ml")
    (tmp / "fit_pipeline.py").write_text(FIT_SCRIPT.format(csv=str(CSV)))
    bp = tmp / "bp.txt"
    r = _run(xorq_bin, "build", tmp / "fit_pipeline.py",
             "--builds-dir", tmp / "builds_ml", "--emit-build-path-to", bp)
    assert r.returncode == 0, r.stderr
    cat = tmp / "cat"
    _run(xorq_bin, "catalog", "-p", cat, "init")
    r = _run(xorq_bin, "catalog", "-p", cat, "add", bp.read_text().strip(), "-a", "tier-model")
    assert r.returncode == 0, r.stdout + r.stderr
    return cat, "tier-model"


def test_fitted_predict_build_is_expr_builder(xorq_bin: str, fitted_entry: tuple) -> None:
    """§A: the predict expr is already tagged, so BUILD-ADD mints kind `expr_builder` and
    `show` reports a fitted_pipeline builder (the skill's Verify expectation)."""
    cat, alias = fitted_entry
    kinds = _run(xorq_bin, "catalog", "-p", cat, "list", "--kind").stdout
    assert "expr_builder" in kinds, kinds
    show = _run(xorq_bin, "catalog", "-p", cat, "show", alias).stdout
    assert "Expression Builder" in show, show
    assert "fitted_pipeline" in show, show


def test_recover_predicts_on_new_data(xorq_bin: str, fitted_entry: tuple) -> None:
    """§B (RECOVER): `run -c 'source.ls.builder.predict(...)'` recovers the live
    FittedPipeline and predicts on new data -> prediction rows."""
    cat, alias = fitted_entry
    r = _run(
        xorq_bin, "catalog", "-p", cat, "run", alias, "--use-this-venv",
        "-c", f"source.ls.builder.predict(xo.deferred_read_csv({str(CSV)!r}))",
        "-o", "-", "-f", "json", "--limit", "5",
    )
    assert r.returncode == 0, r.stdout + r.stderr
    rows = [json.loads(l) for l in r.stdout.splitlines() if l.strip().startswith("{")]
    assert rows, r.stdout + r.stderr
    # the prediction column carries tier labels from the training data
    assert all(isinstance(row.get("predict"), str) for row in rows), rows
