#!/usr/bin/env python3
"""PostToolUse hook — xorq-aware file tracking and skill suggestion.

Triggered after Edit/Write/MultiEdit. Detects xorq-related Python edits and
suggests plugin skills when relevant patterns appear.
Also tracks edited files for the Stop hook to check uncataloged builds.
"""

import json
import os
import re
import sys
from pathlib import Path

# Content patterns that indicate xorq expression work
XORQ_CONTENT_PATTERNS: tuple[str, ...] = (
    r"import xorq",
    r"xorq\.api",
    r"xo\.connect",
    r"xo\._",
    r"from xorq\.vendor import ibis",
    r"deferred_read_parquet",
    r"deferred_read_csv",
    r"ParquetTTLSnapshotCache",
    r"xorq build",
    r"xorq catalog",
    r"xorq run",
    r"from_ibis",
    r"Pipeline\.from_instance",
    r"\.cache\(",
    r"\.execute\(\)",
    r"print\(.*\.schema\(\)\)",
    r"into_backend",
    r"make_pandas_udf",
    r"make_udxf",
)

# Path patterns that indicate xorq-related files
XORQ_PATH_PATTERNS: tuple[str, ...] = (
    r"expr.*\.py$",
    r"pipeline.*\.py$",
    r"xorq.*\.py$",
    r"\.xorq/",
)


def is_xorq_file(file_path: str) -> bool:
    """Check if file path matches xorq-related patterns."""
    return any(re.search(p, file_path) for p in XORQ_PATH_PATTERNS)


def content_has_xorq(tool_input: dict) -> bool:
    """Check if the edit content contains xorq patterns."""
    content = tool_input.get("new_string", "") or tool_input.get("content", "")
    return any(re.search(p, content) for p in XORQ_CONTENT_PATTERNS)


def track_edited_file(file_path: str, session_id: str, project_dir: str) -> None:
    """Log edited file for downstream hooks (Stop check, etc.)."""
    cache_dir = Path(project_dir) / ".claude" / "cache" / (session_id or "default")
    cache_dir.mkdir(parents=True, exist_ok=True)
    log_file = cache_dir / "edited-files.log"
    with open(log_file, "a") as f:
        f.write(f"{file_path}\n")


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", "")
    file_path = tool_input.get("file_path", "")

    # only process edit/write tools
    if tool_name not in ("Edit", "MultiEdit", "Write"):
        return 0

    if not file_path:
        return 0

    # skip non-Python files
    if not file_path.endswith(".py"):
        return 0

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())

    # track every Python edit
    track_edited_file(file_path, session_id, project_dir)

    # check if this is xorq-related
    is_xorq = is_xorq_file(file_path) or content_has_xorq(tool_input)

    if is_xorq:
        print(
            "-------------------------------------------\n"
            "XORQ EXPRESSION DETECTED\n"
            "-------------------------------------------\n"
            "\n"
            "This file contains xorq expression patterns.\n"
            "\n"
            "Relevant skills:\n"
            "  -> /xorq:init             — ingest source data\n"
            "  -> /xorq:builder          — ML pipelines, BSL, TagHandlers\n"
            "  -> /xorq:run-expression   — build and run expressions\n"
            "\n"
            "Reminders:\n"
            "  - Always: from xorq.vendor import ibis (NOT standalone ibis)\n"
            "  - Always: print(table.schema()) before operations\n"
            "  - Always: .execute() only at the end\n"
            "  - Cache after expensive steps: .cache(ParquetTTLSnapshotCache.from_kwargs())\n"
            "-------------------------------------------"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
