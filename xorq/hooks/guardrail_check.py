#!/usr/bin/env python3
"""PreToolUse hook — enforces guardrail rules from skill-rules.json.

Blocks Edit/Write operations that match anti-patterns defined as guardrail
rules. Exit code 2 blocks the tool; stderr message is shown to Claude.

For Bash commands, detects non-xorq data tool usage (pandas, polars, raw
duckdb/postgres/snowflake) and prints a suggestion to use xorq instead
(exit 0 — nudge, not block).
"""

import json
import os
import re
import sys
from pathlib import Path, PurePath

PRIORITY_ORDER = ("critical", "high", "medium", "low")

# Non-xorq data tool patterns detected in Bash commands.
# Each entry: (pattern, tool_name, suggested_skill, fix_message)
NON_XORQ_DATA_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    # pandas
    (r"import pandas|from pandas|pd\.read_csv|pd\.read_parquet|pd\.DataFrame|pd\.read_sql",
     "pandas", "/xorq:init",
     "Use xo.deferred_read_csv() / xo.deferred_read_parquet() to create deferred expressions"),
    # polars
    (r"import polars|from polars|pl\.read_csv|pl\.read_parquet|pl\.scan_csv|pl\.DataFrame",
     "polars", "/xorq:init",
     "Use xo.deferred_read_csv() / xo.deferred_read_parquet() for xorq-managed expressions"),
    # raw duckdb
    (r"import duckdb|duckdb\.connect|duckdb\.sql|duckdb\.read_csv",
     "duckdb (direct)", "/xorq:init",
     "Use xo.connect() with deferred_read_csv/deferred_read_parquet — keeps expressions deferred and catalogable"),
    # raw postgres
    (r"import psycopg|from psycopg|psql\s|pg_dump|pg_restore|create_engine.*postgres",
     "postgres (direct)", "/xorq:init",
     "Use xo.connect('postgres://...') with deferred_read_csv — xorq handles Postgres as a backend"),
    # raw snowflake
    (r"import snowflake|from snowflake|snowsql\s|create_engine.*snowflake",
     "snowflake (direct)", "/xorq:init",
     "Use xo.connect() with deferred_read_csv — xorq handles Snowflake as a backend"),
    # standalone ibis (not from xorq.vendor)
    (r"(?<!xorq\.vendor )import ibis(?!\.)|(?<!\.)ibis\.connect|pip install ibis",
     "standalone ibis", "/xorq:run-expression",
     "Use 'from xorq.vendor import ibis' — standalone ibis has version conflicts"),
    # pip install competing tools
    (r"pip install\s+(pandas|polars|duckdb|ibis-framework)",
     "non-xorq dependency", "/xorq:init",
     "xorq bundles its own backends — installing standalone packages causes conflicts"),
)


def load_guardrails() -> tuple[tuple[str, dict], ...]:
    """Load guardrail rules (type=guardrail, enforcement=block) from skill-rules.json."""
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root:
        return ()
    rules_path = Path(plugin_root) / "hooks" / "skill-rules.json"
    if not rules_path.exists():
        return ()
    try:
        with open(rules_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError):
        return ()

    return tuple(
        (name, config)
        for name, config in data.get("skills", {}).items()
        if config.get("type") == "guardrail" and config.get("enforcement") == "block"
    )


def is_skipped(content: str, config: dict, session_id: str, rule_name: str, project_dir: str) -> bool:
    """Check skip conditions: file markers, env override, session tracking."""
    skip = config.get("skipConditions", {})

    # file marker skip
    for marker in skip.get("fileMarkers", ()):
        if marker in content:
            return True

    # env override
    env_var = skip.get("envOverride", "")
    if env_var and os.environ.get(env_var):
        return True

    # session tracking — skip if already triggered this session
    if skip.get("sessionSkillUsed") and session_id:
        state_dir = Path(project_dir) / ".claude" / "hooks" / "state"
        state_file = state_dir / f"guardrails-{session_id}.json"
        if state_file.exists():
            try:
                used = json.loads(state_file.read_text())
                if rule_name in used.get("rules_triggered", []):
                    return True
            except (json.JSONDecodeError, OSError):
                pass

    return False


def record_trigger(rule_name: str, session_id: str, project_dir: str) -> None:
    """Record that a guardrail was triggered this session."""
    if not session_id:
        return
    state_dir = Path(project_dir) / ".claude" / "hooks" / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / f"guardrails-{session_id}.json"

    try:
        state = json.loads(state_file.read_text()) if state_file.exists() else {}
    except (json.JSONDecodeError, OSError):
        state = {}

    triggered = state.get("rules_triggered", [])
    if rule_name not in triggered:
        triggered.append(rule_name)
    state["rules_triggered"] = triggered
    state_file.write_text(json.dumps(state))


def check_violations(
    content: str,
    file_path: str,
    guardrails: tuple[tuple[str, dict], ...],
    session_id: str,
    project_dir: str,
) -> tuple[tuple[str, dict], ...]:
    """Check content against guardrail rules, return violations sorted by priority."""
    violations = []

    for name, config in guardrails:
        triggers = config.get("fileTriggers", {})

        # path pattern match
        path_patterns = triggers.get("pathPatterns", ["**/*.py"])
        if not any(PurePath(file_path).match(p) for p in path_patterns):
            continue

        # path exclusion check
        exclusions = triggers.get("pathExclusions", ())
        if any(PurePath(file_path).match(p) for p in exclusions):
            continue

        # content pattern match (ANY match triggers)
        content_patterns = triggers.get("contentPatterns", ())
        if not any(re.search(p, content, re.MULTILINE | re.DOTALL) for p in content_patterns):
            continue

        # skip conditions
        if is_skipped(content, config, session_id, name, project_dir):
            continue

        violations.append((name, config))
        record_trigger(name, session_id, project_dir)

    # sort by priority
    priority_rank = {p: i for i, p in enumerate(PRIORITY_ORDER)}
    return tuple(sorted(violations, key=lambda v: priority_rank.get(v[1].get("priority", "low"), 99)))


def check_bash_data_tools(command: str) -> list[tuple[str, str, str]]:
    """Check a Bash command for non-xorq data tool usage.

    Returns list of (tool_name, suggested_skill, fix_message) for matches.
    """
    matches = []
    for pattern, tool_name, skill, fix in NON_XORQ_DATA_PATTERNS:
        if re.search(pattern, command, re.IGNORECASE):
            matches.append((tool_name, skill, fix))
    return matches


def format_bash_nudge(matches: list[tuple[str, str, str]]) -> str:
    """Format a suggestion message for non-xorq data tool usage in Bash."""
    tools = ", ".join(m[0] for m in matches)
    lines = [
        "-------------------------------------------",
        "XORQ DATA TOOL GUIDANCE",
        "-------------------------------------------",
        "",
        f"Detected non-xorq data tools: {tools}",
        "",
        "This project uses xorq for all data work.",
        "xorq expressions are deferred, catalogable, and portable.",
        "",
    ]
    seen_skills = []
    for tool_name, skill, fix in matches:
        lines.append(f"  [{tool_name}] {fix}")
        if skill not in seen_skills:
            seen_skills.append(skill)
    lines.append("")
    lines.append("Use these skills instead:")
    for skill in seen_skills:
        lines.append(f"  -> {skill}")
    lines.append("-------------------------------------------")
    return "\n".join(lines)


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return 0

    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {})
    session_id = data.get("session_id", "")

    # global override
    if os.environ.get("SKIP_XORQ_GUARDRAILS"):
        return 0

    # --- Bash: nudge away from non-xorq data tools (exit 0, not block) ---
    if tool_name == "Bash":
        command = tool_input.get("command", "")
        if not command:
            return 0
        matches = check_bash_data_tools(command)
        if matches:
            print(format_bash_nudge(matches))
        return 0

    # --- Edit/Write: hard guardrail checks (exit 2 = block) ---
    if tool_name not in ("Edit", "MultiEdit", "Write"):
        return 0

    file_path = tool_input.get("file_path", "")
    if not file_path or not file_path.endswith(".py"):
        return 0

    # ensure absolute path for glob matching
    if not PurePath(file_path).is_absolute():
        file_path = str(Path.cwd() / file_path)

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR", os.getcwd())
    guardrails = load_guardrails()
    if not guardrails:
        return 0

    # get content to check — new_string (Edit) or content (Write)
    content = tool_input.get("new_string", "") or tool_input.get("content", "")
    if not content:
        return 0

    violations = check_violations(content, file_path, guardrails, session_id, project_dir)
    if not violations:
        return 0

    # format block messages
    messages = []
    for name, config in violations:
        msg = config.get("blockMessage", f"Guardrail '{name}' violated in {{file_path}}")
        msg = msg.replace("{file_path}", file_path)
        messages.append(msg)

    print("\n\n".join(messages), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
