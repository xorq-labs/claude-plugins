"""Contract tests for the xorq CLI behaviors documented in ``xorq/CLAUDE.md``.

Scope: ONLY the behaviors that CLAUDE.md's "Catalog Resolution" section
explicitly relies on. This is intentionally *not* an exhaustive test of xorq —
it is a **drift alarm**. If one of these fails, xorq's CLI changed and
``xorq/CLAUDE.md`` must be updated so the ambient context Claude reads stays
accurate.

Each test names the exact CLAUDE.md claim it pins. Verified against xorq 0.3.28.
"""

import subprocess


# --- Ambient default precedence: env var > config file > built-in "default" ---
# CLAUDE.md: "A bare `xorq catalog …` resolves the default catalog name with this
# precedence: 1. XORQ_DEFAULT_CATALOG env var  2. ~/.config/xorq/catalog-default
# 3. built-in 'default'."


def test_default_clean_is_builtin(xorq):
    """No env var and no config file -> falls through to built-in 'default'."""
    r = xorq.run("catalog", "default")
    assert r.code == 0, r.output
    assert "default" in r.output
    assert "source: built-in" in r.output


def test_default_set_persists_to_config_file(xorq):
    """`catalog default --set` writes ~/.config/xorq/catalog-default and is reported as the source."""
    assert not xorq.catalog_default_file.exists()

    r = xorq.run("catalog", "default", "--set", "foo")
    assert r.code == 0, r.output
    assert xorq.catalog_default_file.read_text().strip() == "foo"

    r = xorq.run("catalog", "default")
    assert r.code == 0, r.output
    assert "foo" in r.output
    assert "source: config" in r.output


def test_env_var_overrides_config_file(xorq):
    """XORQ_DEFAULT_CATALOG wins even when a config-file default is set (env > config)."""
    xorq.run("catalog", "default", "--set", "foo")  # config says 'foo'

    r = xorq.run("catalog", "default", env={"XORQ_DEFAULT_CATALOG": "bar"})
    assert r.code == 0, r.output
    assert "bar" in r.output
    assert "source: env" in r.output


def test_default_unset_reverts_to_builtin(xorq):
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
# CLAUDE.md: "-n / -p / -u / -r are global flags on the `xorq catalog` group: they go
# BEFORE the subcommand. -n<->-p and -n<->-u are mutually exclusive; -u may pair with -p
# (clone destination); -r requires exactly one of -n/-u and cannot combine with -p."


def test_name_and_path_are_mutually_exclusive(xorq):
    r = xorq.run("catalog", "-n", "a", "-p", "b", "list", "--kind")
    assert r.code != 0
    assert "mutually exclusive" in r.output


def test_name_and_url_are_mutually_exclusive(xorq):
    """CLAUDE.md: `-n` and `-u` are mutually exclusive (can't name a clone target)."""
    r = xorq.run("catalog", "-n", "a", "-u", "https://example.com/r.git", "list", "--kind")
    assert r.code != 0
    assert "mutually exclusive" in r.output


def test_root_repo_requires_name_or_url(xorq, tmp_path):
    """CLAUDE.md: `-r` must pair with exactly one of `-n`/`-u`, never `-p`.

    `-r`'s root is opened as a git repo, so it must be one (git init); pairing it with `-p`
    is then the rejected combination.
    """
    root = tmp_path / "root"
    root.mkdir()
    subprocess.run(["git", "init", str(root)], capture_output=True)
    r = xorq.run("catalog", "-r", str(root), "-p", str(tmp_path / "b"), "list", "--kind")
    assert r.code != 0
    assert "exactly one of" in r.output  # "...provide exactly one of `name` or `url`..."


def test_group_flags_must_precede_the_subcommand(xorq, tmp_path):
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
# CLAUDE.md: "The CLI does not auto-create catalogs; you init them explicitly."


def test_missing_catalog_is_not_autocreated(xorq, tmp_path):
    missing = tmp_path / "nope"
    r = xorq.run("catalog", "-p", str(missing), "list", "--kind")
    assert r.code != 0
    assert "Catalog not found" in r.output
    assert "init" in r.output  # error tells the user the exact `... init` command
    assert not missing.exists()  # nothing was created on disk


# --- Named-catalog location: ~/.local/share/xorq/catalogs/<name> ---
# CLAUDE.md locations table: "Named catalogs root -> ~/.local/share/xorq/catalogs/<name>".


def test_named_catalog_lives_under_xdg_data_home(xorq):
    r = xorq.run("catalog", "-n", "mycat", "init")
    assert r.code == 0, r.output
    assert (xorq.named_catalog_dir("mycat") / "catalog.yaml").exists()
