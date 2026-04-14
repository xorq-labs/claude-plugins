---
description: Explore a xorq catalog — list entries, inspect schemas, and understand available data expressions. Use when the user asks about what's in a catalog, what expressions are available, or wants to understand a pipeline's inputs and outputs.
---

# Catalog Explorer

Use the xorq MCP tools to help the user explore their catalog.

## Workflow

1. **Discover**: Call `catalog_list` to see all entries. If the user specified a catalog name or path, pass it through.
2. **Inspect**: For entries of interest, call `catalog_schema` with `as_json=true` to get the full schema including input/output column names and types.
3. **Context**: Call `catalog_info` to understand the catalog's location, remotes, and size.
4. **History**: If the user wants to understand what changed, call `catalog_log` for structured operation history.

## Presenting results

- Summarize entries in a table: name, kind (source vs unbound), and key columns.
- When showing schemas, highlight the input parameters (schema_in) vs output columns (schema_out).
- If an entry is "unbound" (partial), explain that it requires input data to run.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a catalog name or entry name to focus on.
