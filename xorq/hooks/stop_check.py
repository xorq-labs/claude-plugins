#!/usr/bin/env python3
"""Stop hook — one-time-per-session reminder to catalog expressions worth keeping.

Fires exactly once per session. Uses a state file to track whether the
reminder has already been shown. Only triggers if xorq-related files were
edited during the session.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

STATE_DIR = ".claude/hooks/state"
STATE_PREFIX = "catalog-reminder-fired-"


def already_fired(project_dir: str, session_id: str) -> bool:
    state_file = Path(project_dir) / STATE_DIR / f"{STATE_PREFIX}{session_id}"
    return state_file.exists()


def mark_fired(project_dir: str, session_id: str) -> None:
    state_dir = Path(project_dir) / STATE_DIR
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / f"{STATE_PREFIX}{session_id}").touch()


def get_session_xorq_edits(project_dir: str, session_id: str) -> tuple[str, ...]:
    log_file = (
        Path(project_dir) / ".claude" / "cache" / (session_id or "default") / "edited-files.log"
    )
    if not log_file.exists():
        return ()
    return tuple(line for line in log_file.read_text().strip().split("\n") if line.strip())


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    session_id = data.get("session_id", "")
    if not session_id:
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    if already_fired(project_dir, session_id):
        return 0

    edits = get_session_xorq_edits(project_dir, session_id)
    if not edits:
        return 0

    mark_fired(project_dir, session_id)

    py_edits = [f for f in edits if f.endswith(".py")]
    if not py_edits:
        return 0

    files_list = "\n".join(f"  - {f}" for f in py_edits[-10:])
    print(
        "-------------------------------------------\n"
        "CATALOG REMINDER (one-time)\n"
        "-------------------------------------------\n"
        "\n"
        "You edited xorq-related Python files this session:\n"
        f"{files_list}\n"
        "\n"
        "If any expressions are worth keeping, catalog them:\n"
        "  1. xorq build <file>.py -e expr\n"
        "  2. xorq catalog add builds/<hash> -a <alias>\n"
        "  3. xorq catalog list --kind\n"
        "\n"
        "Skills: /xorq:run-expression, /xorq:catalog-explore\n"
        "-------------------------------------------"
    )

    return 0


if __name__ == "__main__":
    sys.exit(main())
