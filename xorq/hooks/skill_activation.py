#!/usr/bin/env python3
"""UserPromptSubmit hook — skill activation for xorq plugin.

Reads stdin JSON (prompt, session_id, …), matches against skill-rules.json,
prints a suggestion box to stdout.
"""

import json
import os
import re
import sys
from pathlib import Path

PRIORITY_ORDER = ("critical", "high", "medium", "low")
PRIORITY_LABELS = {
    "critical": "CRITICAL SKILLS (REQUIRED)",
    "high": "RECOMMENDED SKILLS",
    "medium": "SUGGESTED SKILLS",
    "low": "OPTIONAL SKILLS",
}
PRIORITY_ICONS = {
    "critical": "!!",
    "high": ">>",
    "medium": "->",
    "low": "..",
}


def load_skill_rules() -> dict:
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "")
    if not plugin_root:
        return {}
    rules_path = Path(plugin_root) / "hooks" / "skill-rules.json"
    if not rules_path.exists():
        return {}
    with open(rules_path) as f:
        return json.load(f)


def match_skills(prompt: str, rules: dict) -> tuple[tuple[str, str, dict], ...]:
    """Return tuple of (skill_name, match_type, config) for matching domain skills."""
    prompt_lower = prompt.lower()
    skills = rules.get("skills", {})
    matched = []

    for skill_name, config in skills.items():
        # only match domain skills (not guardrails)
        if config.get("type") != "domain":
            continue

        triggers = config.get("promptTriggers")
        if not triggers:
            continue

        # keyword matching
        keywords = triggers.get("keywords", ())
        if any(kw.lower() in prompt_lower for kw in keywords):
            matched.append((skill_name, "keyword", config))
            continue

        # intent pattern matching
        patterns = triggers.get("intentPatterns", ())
        if any(re.search(p, prompt, re.IGNORECASE) for p in patterns):
            matched.append((skill_name, "intent", config))

    return tuple(matched)


def format_suggestions(matched: tuple[tuple[str, str, dict], ...]) -> str:
    if not matched:
        return ""

    groups: dict[str, list[str]] = {p: [] for p in PRIORITY_ORDER}
    for name, _match_type, config in matched:
        priority = config.get("priority", "medium")
        groups.setdefault(priority, []).append(name)

    lines = [
        "-------------------------------------------",
        "SKILL ACTIVATION CHECK",
        "-------------------------------------------",
        "",
    ]

    for priority in PRIORITY_ORDER:
        names = groups.get(priority, [])
        if not names:
            continue
        icon = PRIORITY_ICONS[priority]
        label = PRIORITY_LABELS[priority]
        lines.append(f"{icon} {label}:")
        lines.extend(f"  -> /{n}" for n in names)
        lines.append("")

    lines.append("ACTION: Use Skill tool BEFORE responding")
    lines.append("-------------------------------------------")
    return "\n".join(lines)


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        data = {}

    prompt = data.get("prompt", "")
    rules = load_skill_rules()
    matched = match_skills(prompt, rules)
    suggestion = format_suggestions(matched)
    if suggestion:
        print(suggestion)

    return 0


if __name__ == "__main__":
    sys.exit(main())
