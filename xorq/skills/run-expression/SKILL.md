---
description: Build and run xorq data expressions. Use when the user wants to execute a pipeline, build a script into artifacts, or get data results from an expression.
---

# Run Expression

Use the xorq CLI to build and/or run data expressions.

## Building from a script

If the user has a Python script with a xorq expression:

```bash
xorq build <script.py>
```

- The default expression variable name is `expr`. Use `-e <name>` for a different variable.
- Build output goes to `builds/<hash>/`. Note the path for subsequent run commands.
- Use `--debug` to output SQL files for inspection.

## Running a built expression

```bash
xorq run <build_path> -f json --limit 20
```

- `-f` / `--format`: Output format — `json`, `csv`, `parquet`, or `arrow` (default: `parquet`)
- `--limit`: Max rows to return — start with 10-20 unless the user wants everything
- `-p` / `--params`: Pass parameters as `key=value` (repeatable)
- `-o` / `--output-path`: Write output to a file instead of stdout

Example with parameters:

```bash
xorq run <build_path> -f json --limit 10 -p threshold=0.5 -p category=electronics
```

## Running with caching

For repeated runs or large datasets, use caching:

```bash
xorq run-cached <build_path> -f json --limit 20
```

- `--cache-type modification-time` (default): Re-runs when source file modification time changes
- `--cache-type snapshot`: Content-based cache, use with `--ttl` for periodic refresh

```bash
xorq run-cached <build_path> -f json --cache-type snapshot --ttl 3600
```

## Running catalog entries directly

Run an entry without a separate build step:

```bash
xorq catalog run <entry-name> -f json --limit 20
```

Compose and run in one step:

```bash
xorq catalog run <source> <transform> -f json --limit 20
```

With inline code:

```bash
xorq catalog run <source> -c "source.filter(source.amount > 100)" -f json --limit 20
```

Options:
- `--fuse` / `--no-fuse`: Enable/disable source fusion optimization (default: enabled)
- `--rename-params <entry>,<old>,<new>`: Resolve parameter name collisions

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a build path, script path, or entry name.
