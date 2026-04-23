#!/usr/bin/env python3
"""UserPromptSubmit hook — ensure uv, pyproject.toml, and venv with deps exist.

Chain: uv installed? → pyproject.toml exists? → .venv exists? → deps importable?
Prints remediation commands for Claude to run.
"""

import json
import os
import shutil
import subprocess
import sys

DEFAULT_PYPROJECT = """\
[project]
name = "xorq-workspace"
version = "0.1.0"
description = "xorq workspace"
requires-python = ">=3.10,<3.14"
dependencies = [
    "xorq",
    "boring-semantic-layer @ git+https://github.com/boringdata/boring-semantic-layer.git@main",
    "scikit-learn",
]

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.setuptools]
py-modules = []
"""

REQUIRED_PACKAGES = ("xorq", "boring_semantic_layer", "sklearn")


def check_uv() -> str | None:
    if shutil.which("uv"):
        return None
    return (
        "uv is not installed. Install it:\n"
        "  curl -LsSf https://astral.sh/uv/install.sh | sh"
    )


def check_pyproject(project_dir: str) -> str | None:
    toml_path = os.path.join(project_dir, "pyproject.toml")
    if os.path.isfile(toml_path):
        return None
    return (
        f"No pyproject.toml found in {project_dir}.\n"
        "Create one with the xorq defaults:\n"
        f'  cat > "{toml_path}" << \'TOML\'\n'
        f"{DEFAULT_PYPROJECT}TOML\n"
        "\n"
        "Then run: uv sync --python 3.12"
    )


def check_venv(project_dir: str) -> str | None:
    venv = os.path.join(project_dir, ".venv")
    if not os.path.isdir(venv):
        return (
            f"No .venv found in {project_dir}.\n"
            "Create it:\n"
            "  uv sync --python 3.12"
        )

    python = os.path.join(venv, "bin", "python3")
    if not os.path.isfile(python):
        return (
            f".venv exists but python3 binary is missing in {venv}/bin/.\n"
            "Recreate it:\n"
            "  rm -rf .venv && uv sync --python 3.12"
        )

    return None


def check_virtual_env(project_dir: str) -> str | None:
    """Warn if VIRTUAL_ENV points somewhere other than the project's .venv."""
    current = os.environ.get("VIRTUAL_ENV")
    project_venv = os.path.join(project_dir, ".venv")
    if current and os.path.isdir(project_venv) and os.path.realpath(current) != os.path.realpath(project_venv):
        return (
            f"VIRTUAL_ENV is set to {current} but the project venv is {project_venv}.\n"
            "This causes uv and xorq CLI to target the wrong environment.\n"
            "Fix it before running xorq commands:\n"
            f'  export VIRTUAL_ENV="{project_venv}"\n'
            f'  export PATH="{project_venv}/bin:$PATH"'
        )
    return None


def check_deps(project_dir: str) -> str | None:
    venv = os.path.join(project_dir, ".venv")
    python = os.path.join(venv, "bin", "python3")

    missing = []
    for pkg in REQUIRED_PACKAGES:
        try:
            result = subprocess.run(
                [python, "-c", f"import {pkg}"],
                capture_output=True,
                timeout=10,
            )
            if result.returncode != 0:
                missing.append(pkg)
        except (subprocess.TimeoutExpired, OSError):
            missing.append(pkg)

    if not missing:
        return None

    display = ", ".join(
        p.replace("boring_semantic_layer", "boring-semantic-layer")
         .replace("sklearn", "scikit-learn")
        for p in missing
    )
    return (
        f".venv is missing required packages: {display}\n"
        "Install them:\n"
        "  uv sync --python 3.12\n"
        "\n"
        "Or if no pyproject.toml with these deps exists:\n"
        "  uv pip install xorq boring-semantic-layer scikit-learn"
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    # Chain: uv → pyproject.toml → .venv → deps
    checks = [
        check_uv,
        lambda: check_pyproject(project_dir),
        lambda: check_venv(project_dir),
        lambda: check_virtual_env(project_dir),
        lambda: check_deps(project_dir),
    ]

    for check_fn in checks:
        result = check_fn()
        if result:
            print(
                "-------------------------------------------\n"
                "XORQ ENVIRONMENT SETUP NEEDED\n"
                "-------------------------------------------\n"
                "\n"
                f"{result}\n"
                "\n"
                "Fix this before proceeding with xorq commands.\n"
                "-------------------------------------------"
            )
            return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
