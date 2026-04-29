---
description: Explore a xorq catalog — list entries, inspect schemas, and understand available data expressions. Use when the user asks about what's in a catalog, what expressions are available, or wants to understand a pipeline's inputs and outputs.
---

# Catalog Explorer

Use the xorq CLI to help the user explore their catalog.

## Workflow

### 1. Discover entries

List all entries with their kinds:

```bash
xorq catalog list --kind
```

If the user specified a catalog name or path, pass it through:

```bash
xorq catalog list --kind -n <catalog-name>
# or
xorq catalog list --kind -p <catalog-path>
```

### 2. Inspect schemas

For entries of interest, get the full schema as JSON:

```bash
xorq catalog schema <name> --json
```

This shows `schema_in` (input parameters) and `schema_out` (output columns) with types.

### 3. Catalog context

Understand the catalog's location, remotes, and size:

```bash
xorq catalog info
```

### 4. History

View structured operation history:

```bash
xorq catalog log --json
```

### 5. Consistency check

Validate that entries, aliases, metadata, and archives are consistent:

```bash
xorq catalog check
```

## Presenting results

- Summarize entries in a table: name, kind (Source, UnboundExpr, Composed, ExprBuilder), and key columns.
- When showing schemas, highlight the input parameters (`schema_in`) vs output columns (`schema_out`).
- If an entry is `UnboundExpr` (partial), explain that it requires input data to run — it's a reusable transform.
- If an entry is `ExprBuilder`, note that it contains a recoverable domain object (e.g., ML pipeline, semantic model).
- If an entry is `Composed`, note that it was assembled from other catalog entries.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a catalog name or entry name to focus on.
