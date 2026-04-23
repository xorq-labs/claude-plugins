#!/usr/bin/env python3
"""PostToolUse hook — xorq troubleshooting on failure.

Triggered after Edit/MultiEdit/Write/Bash. When the tool result indicates
an error in xorq-related code, prints troubleshooting guidance to stderr.
"""

import json
import re
import sys

XORQ_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"ModuleNotFoundError.*ibis", "Use 'from xorq.vendor import ibis' (NOT standalone ibis)"),
    (r"AttributeError.*'Schema'", "Check table.schema() — column names may differ"),
    (r"ColumnNotFound|IbisInputError", "Inspect schema with print(table.schema()) first"),
    (r"ArrowInvalid|ArrowTypeError", "Type mismatch — check dtypes with .type() on columns"),
    (r"xorq.*build.*error|BuildError", "Verify the -e variable name matches your expression"),
    (r"CatalogError|catalog.*not found", "Run 'xorq catalog list' to check available entries"),
    (r"FlightUnavailableError", "Flight server not running — check xorq serve status"),
    (r"UDF.*error|udxf.*error", "UDF type coercion — try .into_backend(con=xo.connect()) before UDF"),
)


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except (json.JSONDecodeError, OSError):
        return 0

    tool_result = str(data.get("tool_result", ""))
    tool_input = data.get("tool_input", {})

    # only trigger when the result looks like an error
    if not re.search(r"(?i)error|exception|traceback|failed|errno", tool_result):
        return 0

    # gather all context: file path, command, content, and result
    context_parts = (
        tool_input.get("file_path", ""),
        tool_input.get("command", ""),
        tool_input.get("new_string", ""),
        tool_input.get("content", ""),
        tool_result,
    )
    combined = " ".join(context_parts)

    # only trigger for xorq-related errors
    if not re.search(r"xorq|ibis|xo\.|deferred_read|ParquetTTLSnapshotCache|\.xorq", combined):
        return 0

    # find matching error patterns
    tips = tuple(
        tip for pattern, tip in XORQ_ERROR_PATTERNS
        if re.search(pattern, tool_result, re.IGNORECASE)
    )

    print("=" * 60, file=sys.stderr)
    print("XORQ TROUBLESHOOTING", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    if tips:
        print("\nMatched fixes:", file=sys.stderr)
        for tip in tips:
            print(f"  * {tip}", file=sys.stderr)
    else:
        print("\nCommon fixes:", file=sys.stderr)
        print("  * Schema errors: Check table.schema() before building", file=sys.stderr)
        print("  * Import errors: Use 'from xorq.vendor import ibis'", file=sys.stderr)
        print("  * Build errors: Verify variable name matches -e argument", file=sys.stderr)

    print("\nRelevant skills:", file=sys.stderr)
    print("  /xorq:run-expression   — build and run expressions", file=sys.stderr)
    print("  /xorq:builder          — ML pipelines, BSL, TagHandlers", file=sys.stderr)
    print("  /xorq:catalog-explore  — inspect catalog entries", file=sys.stderr)

    print("\nQuick commands:", file=sys.stderr)
    print("  xorq build <file>.py -e expr --debug", file=sys.stderr)
    print("  xorq catalog list --kind", file=sys.stderr)
    print("  xorq catalog schema <alias> --json", file=sys.stderr)
    print("=" * 60 + "\n", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
