"""The plugin's SessionStart hook injects the shared essentials into context.

This is the delivery mechanism for the must-be-present vocabulary (catalog resolution +
build-add / recover / verify). It replaces the deleted CLAUDE.md and the per-skill injection:
the hook fires once per session — including headless ``claude -p`` — and prints
``skills/_shared/essentials.md`` to stdout, which Claude Code folds into context.

Pinned deterministically (no LLM): the hook config is auto-discoverable, targets the essentials,
and running its command (with ``CLAUDE_PLUGIN_ROOT`` set, as Claude Code does) emits the essentials
verbatim. If this fails, the ambient vocabulary stops reaching the skills.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGIN = REPO / "xorq"
HOOKS = PLUGIN / "hooks" / "hooks.json"
ESSENTIALS = PLUGIN / "skills" / "_shared" / "essentials.md"


def _sessionstart_commands() -> list[str]:
    cfg = json.loads(HOOKS.read_text())
    return [
        hook["command"]
        for entry in cfg["hooks"]["SessionStart"]
        for hook in entry["hooks"]
        if hook.get("type") == "command"
    ]


def test_essentials_carries_the_idioms() -> None:
    text = ESSENTIALS.read_text()
    for marker in ("Catalog resolution", "BUILD-ADD", "RECOVER", "VERIFY", "XORQ_DEFAULT_CATALOG"):
        assert marker in text, f"essentials.md is missing {marker!r}"


def test_sessionstart_hook_targets_the_essentials() -> None:
    cmds = _sessionstart_commands()
    assert cmds, "no SessionStart command hook in hooks/hooks.json"
    assert any("skills/_shared/essentials.md" in c for c in cmds), cmds


def test_running_the_hook_emits_the_essentials_verbatim() -> None:
    """Run the hook command with CLAUDE_PLUGIN_ROOT set; stdout must equal essentials.md."""
    cmd = next(c for c in _sessionstart_commands() if "essentials.md" in c)
    env = {**os.environ, "CLAUDE_PLUGIN_ROOT": str(PLUGIN)}
    r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert r.stdout == ESSENTIALS.read_text(), "hook stdout must be the essentials verbatim"
