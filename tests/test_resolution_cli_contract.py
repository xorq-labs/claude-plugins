"""Contract tests for the xorq CLI behaviors the shared kernel/reference rely on.

Scope: ONLY the behaviors that ``skills/_shared/kernel.md`` (catalog resolution) and
``skills/_shared/reference.md`` (the flag / precedence / locations detail) explicitly rely
on. This is intentionally *not* an exhaustive test of xorq — it is a **drift alarm**. If one
of these fails, xorq's CLI changed and the kernel/reference must be updated so the vocabulary
the skills inject stays accurate.

Each test names the exact claim it pins. Verified against xorq 0.3.29.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from conftest import XorqCli


# --- Ambient default precedence: env var > config file > built-in "default" ---
# reference.md: "A bare `xorq catalog …` resolves the default catalog name with this
# precedence: 1. XORQ_DEFAULT_CATALOG env var  2. ~/.config/xorq/catalog-default
# 3. built-in 'default'."


def test_default_clean_is_builtin(xorq: XorqCli) -> None:
    """No env var and no config file -> falls through to built-in 'default'."""
    r = xorq.run("catalog", "default")
    assert r.code == 0, r.output
    assert "default" in r.output
    assert "source: built-in" in r.output


def test_default_set_persists_to_config_file(xorq: XorqCli) -> None:
    """`catalog default --set` writes ~/.config/xorq/catalog-default and is reported as the source."""
    assert not xorq.catalog_default_file.exists()

    r = xorq.run("catalog", "default", "--set", "foo")
    assert r.code == 0, r.output
    assert xorq.catalog_default_file.read_text().strip() == "foo"

    r = xorq.run("catalog", "default")
    assert r.code == 0, r.output
    assert "foo" in r.output
    assert "source: config" in r.output


def test_env_var_overrides_config_file(xorq: XorqCli) -> None:
    """XORQ_DEFAULT_CATALOG wins even when a config-file default is set (env > config)."""
    xorq.run("catalog", "default", "--set", "foo")  # config says 'foo'

    r = xorq.run("catalog", "default", env={"XORQ_DEFAULT_CATALOG": "bar"})
    assert r.code == 0, r.output
    assert "bar" in r.output
    assert "source: env" in r.output


def test_default_unset_reverts_to_builtin(xorq: XorqCli) -> None:
    """`--unset` removes the config file and resolution reverts to built-in 'default'."""
    xorq.run("catalog", "default", "--set", "foo")
    assert xorq.catalog_default_file.exists()

    r = xorq.run("catalog", "default", "--unset")
    assert r.code == 0, r.output
    assert not xorq.catalog_default_file.exists()

    r = xorq.run("catalog", "default")
    assert "default" in r.output
    assert "source: built-in" in r.output


# --- Per-command targeting: group flags, placement, combination rules ---
# reference.md: "-n / -p / -u / -r are global flags on the `xorq catalog` group: they go
# BEFORE the subcommand. -n<->-p and -n<->-u are mutually exclusive; -u may pair with -p
# (clone destination); -r requires exactly one of -n/-u and cannot combine with -p."


def test_name_and_path_are_mutually_exclusive(xorq: XorqCli) -> None:
    r = xorq.run("catalog", "-n", "a", "-p", "b", "list", "--kind")
    assert r.code != 0
    assert "mutually exclusive" in r.output


def test_name_and_url_are_mutually_exclusive(xorq: XorqCli) -> None:
    """reference.md: `-n` and `-u` are mutually exclusive (can't name a clone target)."""
    r = xorq.run("catalog", "-n", "a", "-u", "https://example.com/r.git", "list", "--kind")
    assert r.code != 0
    assert "mutually exclusive" in r.output


def test_root_repo_requires_name_or_url(xorq: XorqCli, tmp_path: Path) -> None:
    """reference.md: `-r` must pair with exactly one of `-n`/`-u`, never `-p`.

    `-r`'s root is opened as a git repo, so it must be one (git init); pairing it with `-p`
    is then the rejected combination.
    """
    root = tmp_path / "root"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], capture_output=True)
    r = xorq.run("catalog", "-r", str(root), "-p", str(tmp_path / "b"), "list", "--kind")
    assert r.code != 0
    assert "exactly one of" in r.output  # "...provide exactly one of `name` or `url`..."


def test_group_flags_must_precede_the_subcommand(xorq: XorqCli, tmp_path: Path) -> None:
    """`-p` is a GROUP flag: accepted before the subcommand, rejected after it."""
    missing = tmp_path / "nope"

    # Before the subcommand: parsed fine, fails later at resolution (proves it's accepted here).
    before = xorq.run("catalog", "-p", str(missing), "list", "--kind")
    assert before.code != 0
    assert "Catalog not found" in before.output

    # After the subcommand: `list` has no such option -> a parse error, not a resolution error.
    after = xorq.run("catalog", "list", "--kind", "-p", str(missing))
    assert after.code != 0
    assert "No such option" in after.output


# --- No auto-create: a missing catalog errors with the exact fix, never silently created ---
# reference.md: "The CLI does not auto-create catalogs; you init them explicitly."


def test_missing_catalog_is_not_autocreated(xorq: XorqCli, tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    r = xorq.run("catalog", "-p", str(missing), "list", "--kind")
    assert r.code != 0
    assert "Catalog not found" in r.output
    assert "init" in r.output  # error tells the user the exact `... init` command
    assert not missing.exists()  # nothing was created on disk


# --- Named-catalog location: ~/.local/share/xorq/catalogs/<name> ---
# reference.md locations table: "Named catalogs root -> ~/.local/share/xorq/catalogs/<name>".


def test_named_catalog_lives_under_xdg_data_home(xorq: XorqCli) -> None:
    r = xorq.run("catalog", "-n", "mycat", "init")
    assert r.code == 0, r.output
    assert (xorq.named_catalog_dir("mycat") / "catalog.yaml").exists()
