---
description: Build and run xorq data expressions. Use when the user wants to execute a pipeline, build a script into artifacts, or get data results from an expression.
---

# Run Expression

Use the xorq MCP tools to build and/or run data expressions.

## Building from a script

If the user has a Python script with a xorq expression:
1. Call `build` with the script path. The default expression variable name is `expr`.
2. The build output tells you the build directory path — use it for subsequent `run` calls.

## Running a built expression

1. Call `run` with the build path. Default format is `json` for readability.
2. Use `limit` to avoid overwhelming output — start with 10-20 rows unless the user wants everything.
3. If the expression has parameters, pass them via `params` (e.g. `{"threshold": "0.5"}`).

## Running with caching

For repeated runs or large datasets, prefer `run_cached`:
- Use `cache_type="modification-time"` (default) for source data that changes in place.
- Use `cache_type="snapshot"` with a `ttl` for data that should be refreshed periodically.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a build path or script path.
