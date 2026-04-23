---
description: Compose existing xorq catalog entries into new aliased expressions. Use when the user wants to combine a source with transforms, apply inline code to an entry, or assemble a pipeline from catalog entries.
---

# Composer — Compose Catalog Entries

Guide the user through combining existing catalog entries (source + transforms + optional inline code) into new composed expressions.

## Workflow

### 1. Discover available entries

List entries with their kinds:

```bash
xorq catalog list --kind
```

- **Source** (`source`) — bound, has data; use as the first entry in composition
- **UnboundExpr** (`unbound_expr`) — partial transform, awaits input; use as transform entries
- **Composed** (`composed`) — already composed; can be used as a source
- **ExprBuilder** (`expr_builder`) — ML pipeline or semantic model

### 2. Inspect schemas for compatibility

Check source output schema:

```bash
xorq catalog schema <source-name> --json
```

Check transform input schema:

```bash
xorq catalog schema <transform-name> --json
```

The transform's `schema_in` (input parameters) must be satisfiable by the source's output columns. The ExprComposer validates this automatically.

### 3. Compose

**IMPORTANT:** Only entries with `kind=UnboundExpr` can be used as transform entries. You CANNOT compose two `Source` entries together (e.g., to join them). To join two sources, use inline code (`-c`) on one source and reference the other via Python:

**Joining two source entries (use inline code):**

```bash
xorq catalog compose <source1> -c "
import xorq.api as xo
other = xo.read_csv('path/to/other.csv')
source.join(other, 'join_key').select('col1', 'col2', 'col3')
" -a <alias>
```

Or write a build script that reads both from the catalog and joins them.

**Source + transforms (unbound_expr only):**

```bash
xorq catalog compose <source> <transform1> <transform2> -a <alias>
```

Entries are listed in order: source first, then transforms applied sequentially.

**Source + inline code (most flexible):**

```bash
xorq catalog compose <source> -c "source.filter(source.amount > 15)" -a <alias>
```

The inline code receives the expression as the `source` variable.

**Source + transforms + inline code:**

```bash
xorq catalog compose <source> <transform> -c "source.mutate(doubled=source.amount * 2)" -a <alias>
```

**Preview without cataloging (dry run):**

```bash
xorq catalog compose <source> <transform> --dry-run
```

This shows the composition plan and resulting schema without building or adding to the catalog.

**Resolve parameter name collisions:**

```bash
xorq catalog compose <source> <transform> --rename-params <transform>,old_param,new_param -a <alias>
```

### 4. Verify

Confirm the new entry exists:

```bash
xorq catalog list --kind
```

The new entry should have kind `composed`. Inspect its schema:

```bash
xorq catalog schema <alias> --json
```

## Tips

- Always use `--dry-run` first when unsure about compatibility — it validates without side effects.
- The source entry must have `kind=Source` or `kind=Composed` (anything with bound data).
- Transform entries must have `kind=UnboundExpr` (they contain unbound tables that get replaced by the source).
- Inline code (`-c`) is Ibis expression syntax applied to the `source` variable.
- Use `--rename-params` when two transforms have parameters with the same name but different meanings.
- Composed entries are tagged with `CatalogTag.SOURCE`, `CatalogTag.TRANSFORM`, and `CatalogTag.CODE` for provenance tracking.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as entry names to compose.
