"""Contract tests for the `builder` skill's documented recipes (xorq/skills/builder/SKILL.md).

Drift check, NOT a re-test of xorq: it runs only the public CLI surface the SKILL.md tells the
agent to run, so doc drift against the installed xorq is caught. (xorq's own builder internals —
``_extract_kind`` / ``_resolve_builder_from_tag`` — are exercised by xorq's own test_builders.py,
not here.)

Uses boring-semantic-layer as the builder fixture: BSL ships the ``xorq.from_tag_node`` entry
point, so a ``.to_tagged()`` semantic-model entry round-trips through separate CLI processes with
no custom-package install. Source data is tests/data/transactions.csv (a ``category`` dimension +
an ``amount`` measure).

``catalog add`` builds a wheel of this project, so tests run from the repo root (pytest's rootdir)
in a wheel-buildable env. The round-trip uses ``catalog run --use-this-venv`` (the current venv
has BSL + its entry point) — offline, no isolated-env network. Verified against xorq 0.3.28.

Pins SKILL.md §B (build -> add -> ``expr_builder``) and §A (``run -c
'source.ls.builder.query(...).to_tagged()'`` -> rows). Skips cleanly when xorq / BSL / the fixture
data is absent.
"""

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("boring_semantic_layer")

REPO = Path(__file__).resolve().parent.parent
CSV = REPO / "tests" / "data" / "transactions.csv"

# SKILL.md §B: a build script that produces a tagged semantic-model expression bound to `expr`.
BUILD_SCRIPT = """import xorq.api as xo
from boring_semantic_layer import to_semantic_table
t = xo.deferred_read_csv({csv!r})
model = (
    to_semantic_table(t)
    .with_dimensions(category=lambda t: t.category)
    .with_measures(
        total_amount=lambda t: t.amount.sum(),
        txn_count=lambda t: t.count(),
    )
)
expr = model.query(dimensions=("category",), measures=("total_amount", "txn_count")).to_tagged()
"""


def _run(xorq_bin, *args, timeout=300):
    return subprocess.run(
        [xorq_bin, *map(str, args)], capture_output=True, text=True, timeout=timeout
    )


@pytest.fixture(scope="module")
def builder_entry(xorq_bin, tmp_path_factory):
    """Build the BSL `.to_tagged()` script and add it once; return (catalog, alias). SKILL.md §B."""
    if not CSV.exists():
        pytest.skip("missing tests/data/transactions.csv")
    tmp = tmp_path_factory.mktemp("builder")
    (tmp / "build_builder.py").write_text(BUILD_SCRIPT.format(csv=str(CSV)))
    bp = tmp / "bp.txt"
    r = _run(
        xorq_bin, "build", tmp / "build_builder.py",
        "--builds-dir", tmp / "builds_builder", "--emit-build-path-to", bp,
    )
    assert r.returncode == 0, r.stderr
    cat = tmp / "cat"
    _run(xorq_bin, "catalog", "-p", cat, "init")
    r = _run(xorq_bin, "catalog", "-p", cat, "add", bp.read_text().strip(), "-a", "sem-by-category")
    assert r.returncode == 0, r.stdout + r.stderr
    return cat, "sem-by-category"


def test_build_yields_expr_builder_entry(xorq_bin, builder_entry):
    """§B: a `.to_tagged()` build + `catalog add` is kind `expr_builder`, and `show` reports the
    builder type."""
    cat, alias = builder_entry
    kinds = _run(xorq_bin, "catalog", "-p", cat, "list", "--kind").stdout
    assert "expr_builder" in kinds, kinds
    show = _run(xorq_bin, "catalog", "-p", cat, "show", alias).stdout
    assert "Expression Builder" in show, show
    assert "semantic_model" in show, show  # the builder type, in the "Builders:" block


def test_cli_round_trip_recovers_and_requeries(xorq_bin, builder_entry):
    """§A (flagship example): `run -c 'source.ls.builder.query(...).to_tagged()'` recovers the
    SemanticModel and re-queries it with a different selection -> rows."""
    cat, alias = builder_entry
    r = _run(
        xorq_bin, "catalog", "-p", cat, "run", alias, "--use-this-venv",
        "-c", 'source.ls.builder.query(dimensions=["category"], measures=["txn_count"]).to_tagged()',
        "-o", "-", "-f", "json", "--limit", "5",
    )
    assert r.returncode == 0, r.stderr
    rows = [json.loads(line) for line in r.stdout.splitlines() if line.strip().startswith("{")]
    assert rows, r.stdout + r.stderr
    # the re-query selected only the new measure — not the entry's stored `total_amount`
    assert "txn_count" in rows[0] and "total_amount" not in rows[0], rows[0]
