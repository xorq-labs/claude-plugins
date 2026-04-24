---
description: Compose and run xorq catalog entries. Use when the user wants to combine a source with transforms, apply inline code, run expressions, or build scripts into artifacts.
---

# Composer — Compose, Run, and Build Expressions

Compose existing catalog entries, run them, and optionally catalog the results.

## Quick start: `xorq catalog run`

The fastest way to compose and execute — composes entries and runs them in one step (does not catalog the result):

```bash
xorq catalog run <source> -f json --limit 20
```

Compose and run with inline code:

```bash
xorq catalog run <source> -c "source.filter(source.amount > 100)" -f json --limit 20
```

Compose and run with transforms:

```bash
xorq catalog run <source> <transform> -f json --limit 20
```

Options:
- `-f` / `--format`: Output format — `json`, `csv`, `parquet`, or `arrow` (default: `parquet`)
- `--limit`: Max rows to return — start with 10-20 unless the user wants everything
- `-p` / `--params`: Pass parameters as `key=value` (repeatable)
- `--fuse` / `--no-fuse`: Enable/disable source fusion optimization (default: enabled)
- `--rename-params <entry>,<old>,<new>`: Resolve parameter name collisions

## Composing and cataloging

To compose AND add to the catalog (not just run), use `xorq catalog compose`:

```bash
xorq catalog compose <source> -c "source.filter(source.amount > 15)" -a <alias>
```

**IMPORTANT:** Only entries with `kind=UnboundExpr` can be used as transform entries. You CANNOT compose two `Source` entries together (e.g., to join them). To join two sources, use inline code (`-c`) on one source and reference the other via Python:

**Joining two source entries (use a build script — NOT inline code):**

Inline `-c` code must be a **single expression** (no imports, no assignments). For joins or anything requiring multiple statements, write a build script instead:

```python
# join_sources.py
import xorq.api as xo
from xorq.catalog.catalog import Catalog

con = xo.connect()  # shared connection — required for joins
cat = Catalog.from_default()
source1 = cat.load("entry1", con=con)
source2 = cat.load("entry2", con=con)

expr = source1.join(source2, "join_key").select("col1", "col2", "col3")
```

```bash
xorq build join_sources.py
xorq catalog add builds/<hash> -a <alias>
```

**Source + transforms (unbound_expr only):**

```bash
xorq catalog compose <source> <transform1> <transform2> -a <alias>
```

**Source + inline code (most flexible):**

```bash
xorq catalog compose <source> -c "source.filter(source.amount > 15)" -a <alias>
```

The inline code receives the expression as the `source` variable. **`-c` must be a single expression on one line** — no imports, no assignments, no multiline code. For anything more complex, write a build script.

**Preview without cataloging (dry run):**

```bash
xorq catalog compose <source> <transform> --dry-run
```

**Resolve parameter name collisions:**

```bash
xorq catalog compose <source> <transform> --rename-params <transform>,old_param,new_param -a <alias>
```

## Building from a script

If the user has a Python script with a xorq expression:

```bash
xorq build <script.py>
```

- The default expression variable name is `expr`. Use `-e <name>` for a different variable.
- Build output goes to `builds/<hash>/`. Note the path for subsequent run or catalog add commands.
- Use `--debug` to output SQL files for inspection.

## Running a built expression

```bash
xorq run <build_path> -f json --limit 20
```

With parameters:

```bash
xorq run <build_path> -f json --limit 10 -p threshold=0.5 -p category=electronics
```

## Running with caching

```bash
xorq run-cached <build_path> -f json --limit 20
```

- `--cache-type modification-time` (default): Re-runs when source file modification time changes
- `--cache-type snapshot`: Content-based cache, use with `--ttl` for periodic refresh

## Discovering available entries

List entries with their kinds:

```bash
xorq catalog list --kind
```

- **Source** (`source`) — bound, has data; use as source in composition
- **UnboundExpr** (`unbound_expr`) — partial transform, awaits input
- **Composed** (`composed`) — already composed; can be used as a source
- **ExprBuilder** (`expr_builder`) — ML pipeline or semantic model

Inspect schemas:

```bash
xorq catalog schema <name> --json
```

## Tips

- See CLAUDE.md Common Pitfalls for environment and API issues (VIRTUAL_ENV mismatch, `--no-sync` only for `catalog add`, compose requires `UnboundExpr` transforms, etc.)
- Start with `xorq catalog run` to test compositions before cataloging them with `compose`.
- Always use `--dry-run` on `compose` when unsure about compatibility.
- The source entry must have `kind=Source` or `kind=Composed` (anything with bound data).
- Transform entries must have `kind=UnboundExpr`.
- Inline code (`-c`) is Ibis expression syntax applied to the `source` variable.
- Composed entries are tagged with `CatalogTag.SOURCE`, `CatalogTag.TRANSFORM`, and `CatalogTag.CODE` for provenance tracking.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as entry names to compose or run.
