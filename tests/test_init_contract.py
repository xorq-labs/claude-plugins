"""Contract tests for the `init` skill's no-build acquire/copy behaviors.

Pins what xorq/skills/init/SKILL.md claims, so doc drift is caught:
  - `catalog init`        -> creates an empty catalog (catalog.yaml, zero entries)
  - `replay A <target>`   -> copies entries into the target WITHOUT rebuilding
  - `get <hash>` + `add`  -> transfers a single built archive between catalogs, no rebuild
  - `get` resolves by entry HASH, not by alias (the skill tells users to look the hash up)

A `source` entry is built exactly ONCE here (a CSV via deferred_read_csv); every assertion
proves the entry then moves between catalogs by hash / archive, never by re-running a build.

`catalog add` builds a wheel of this project, so tests run from the repo root (pytest's
rootdir) in a wheel-buildable env. Verified against xorq 0.3.28.
"""

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
DATA = REPO / "tests" / "data"


def _run(xorq_bin, *args):
    return subprocess.run([xorq_bin, *args], capture_output=True, text=True)


def _source_hashes(xorq_bin, cat):
    out = _run(xorq_bin, "catalog", "-p", str(cat), "list", "--kind").stdout
    return [p[0] for line in out.splitlines() if len(p := line.split()) >= 2 and p[1] == "source"]


def _seed_catalog(xorq_bin, tmp_path, name, alias="customers"):
    """Build a CSV source ONCE and add it to a fresh catalog at tmp_path/<name>. Returns the path."""
    if not (DATA / "customers.csv").exists():
        pytest.skip("missing tests/data/customers.csv")
    (tmp_path / "ingest.py").write_text(
        f'import xorq.api as xo\nexpr = xo.deferred_read_csv({str(DATA / "customers.csv")!r})\n'
    )
    bp = tmp_path / "bp.txt"
    r = _run(
        xorq_bin, "build", str(tmp_path / "ingest.py"),
        "--builds-dir", str(tmp_path / "builds_source"), "--emit-build-path-to", str(bp),
    )
    assert r.returncode == 0, r.stderr
    cat = tmp_path / name
    _run(xorq_bin, "catalog", "-p", str(cat), "init")
    r = _run(xorq_bin, "catalog", "-p", str(cat), "add", bp.read_text().strip(), "-a", alias)
    assert r.returncode == 0, r.stdout + r.stderr
    return cat


def test_init_creates_empty_catalog(xorq_bin, tmp_path):
    """`catalog init` creates a catalog.yaml with no entries (no auto-populate)."""
    cat = tmp_path / "cat"
    r = _run(xorq_bin, "catalog", "-p", str(cat), "init")
    assert r.returncode == 0, r.stderr
    assert (cat / "catalog.yaml").exists()
    assert not _source_hashes(xorq_bin, cat)


def test_replay_copies_entry_without_rebuild(xorq_bin, tmp_path):
    """`replay A <target>` reproduces A's source entry in a fresh target — same hash, no build."""
    src = _seed_catalog(xorq_bin, tmp_path, "A")
    (h,) = _source_hashes(xorq_bin, src)
    target = tmp_path / "C"  # does not exist yet; replay creates it
    r = _run(xorq_bin, "catalog", "-p", str(src), "replay", str(target))
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Replayed" in (r.stdout + r.stderr)
    assert h in _source_hashes(xorq_bin, target)  # identical content hash carried over


def test_get_then_add_transfers_single_entry(xorq_bin, tmp_path):
    """`get <hash> -o <dir>` writes <dir>/<hash>.zip; `add` of that archive registers it as-is."""
    src = _seed_catalog(xorq_bin, tmp_path, "A")
    (h,) = _source_hashes(xorq_bin, src)
    exp = tmp_path / "exp"
    exp.mkdir()  # get does NOT create the output dir
    r = _run(xorq_bin, "catalog", "-p", str(src), "get", h, "-o", str(exp))
    assert r.returncode == 0, r.stdout + r.stderr
    assert (exp / f"{h}.zip").exists()

    dst = tmp_path / "B"
    _run(xorq_bin, "catalog", "-p", str(dst), "init")
    r = _run(xorq_bin, "catalog", "-p", str(dst), "add", str(exp / f"{h}.zip"), "-a", "customers")
    assert r.returncode == 0, r.stdout + r.stderr
    assert h in _source_hashes(xorq_bin, dst)


def test_get_requires_hash_not_alias(xorq_bin, tmp_path):
    """`get` resolves by entry hash only — an alias is 'not found' (the skill says: use the hash)."""
    src = _seed_catalog(xorq_bin, tmp_path, "A", alias="customers")
    exp = tmp_path / "exp"
    exp.mkdir()
    r = _run(xorq_bin, "catalog", "-p", str(src), "get", "customers", "-o", str(exp))
    assert r.returncode != 0
    assert "not found" in (r.stdout + r.stderr)
