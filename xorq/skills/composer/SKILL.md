---
description: Compose a new catalog entry from data already in the catalog — take a source entry, optionally apply reusable transform entries and/or an inline Ibis expression, then build and catalog the result in one step (ExprKind "composed"). Use when the inputs already live in a catalog; for raw files/tables use ingest, for fitting models use ml.
---

# Composer — Compose a New Entry from Catalogued Expressions

Take a **source** entry and shape it — with an inline expression, reusable **transform**
entries, or both — into a new content-addressed **`composed`** entry. One command
**builds and catalogs** the result (no separate `catalog add`).

```
xorq catalog -p "$CAT" compose <source> [<transform> …] [-c "<inline expr>"] -a <alias>
```

- **First entry = the source** — its expression is the base (any data-yielding kind:
  `source`, `composed`, `expr`).
- **Remaining entries = transforms**, applied in order. Each must be an **`unbound_expr`**
  entry (see below).
- **`-c` / `--code`** = an inline Ibis expression over the variable `source`.
- Transforms and `--code` combine; **code runs last**. The result is always cataloged as a
  new `composed` entry; `-a` adds an alias.

## Resolve the catalog

Run the **Catalog Resolution** procedure in `xorq/CLAUDE.md` first, then thread the target
on every call (`-p <path>` or `-n <name>`). Below uses `CAT=<catalog>`.

## A. Inline code — ad-hoc, most general

```bash
CAT=<catalog>
xorq catalog -p "$CAT" compose <source> \
  -c "source.filter(source.<col> > <n>).select('<col>', '<other_col>')" \
  -a <alias>
```

- Reference columns as `source.<col>` or `source['<col>']`; chain Ibis ops freely.
- The namespace is **sandboxed**: only `source`, `xo`, `ibis` — no imports, no builtins.
  For anything heavier than an expression chain, make a transform entry (below).

## B. Reusable transform entries — versioned, schema-checked

A transform is an expression over an **unbound** table (schema only, no data), so it can be
reapplied to any compatible source. Built over `xo.table(...)`, it catalogs as
**`unbound_expr`** (its input schema = `schema_in`, its output = `schema_out`):

```python
# transform.py — data-agnostic; binds to a source at compose time
import xorq.api as xo
t = xo.table({"value": "float64", "label": "string"}, name="t")   # unbound: schema only
expr = t.filter(t.value > 0).group_by("label").agg(total=t.value.sum())
```

```bash
xorq build transform.py --builds-dir builds_transform --emit-build-path-to bp.txt
xorq catalog -p "$CAT" add "$(cat bp.txt)" -a totals_by_label      # kind: unbound_expr
```

Apply it to any compatible source (chain several by listing more entries):

```bash
xorq catalog -p "$CAT" compose <source> totals_by_label -a <alias>
```

**Schema rule:** the source's columns must be a **superset** of the transform's `schema_in`
(matched by name + type); chained transforms validate left-to-right and a mismatch lists the
offending columns.

## Joining two catalog entries

`catalog compose` is **single-input** — one source threaded through transforms, and its
`--code` sees only `source`. To **join two catalog entries** (e.g. two related tables),
drop to a build script: load each entry's expression, join them, then `xorq build` +
`xorq catalog add` (the result is an `expr` entry, not `composed`). See **Building
expressions** in `xorq/CLAUDE.md` for the load → build → add ladder.

## Preview without persisting

```bash
xorq catalog -p "$CAT" compose <source> totals_by_label --dry-run   # plan + result schema, no build
```

`compose` otherwise always catalogs. To execute for data **without** creating an entry, use
`xorq catalog run <entries…>` instead.

## Verify

```bash
xorq catalog -p "$CAT" list --kind                 # expect: <hash>  composed
xorq catalog -p "$CAT" show <alias>                # shows "Composed from: N"
xorq catalog -p "$CAT" schema <alias> --json
xorq catalog -p "$CAT" run <alias> -o - -f json --limit 5     # -o - required to print rows
```

## Pitfalls

- **Identical `requirements.txt` across all entries.** The default isolated build merges
  every entry's bundle in `uv tool run`; mismatched pins fail. Build a source and its
  transforms in the same environment.
- **`--use-this-venv`** builds in the current env (faster, no subprocess) but is only correct
  when that env already has every dependency each entry needs.
- **Transforms must be `unbound_expr`** (built over `xo.table(schema=…)`, not over real
  data). A `source`/`composed` entry can only be the *source*, never a transform.
- **`--code` is sandboxed** to `source` / `xo` / `ibis` — no imports or builtins.
- **Always catalogs** — there is no build-only mode; use `--dry-run` to preview or
  `catalog run` to just execute.
- **`--rename-params entry,old,new`** resolves an unbound-parameter name clash on a specific
  entry (repeatable); only needed when chained transforms collide on a param name.
- **`VIRTUAL_ENV` mismatch** → prefix commands with `uv run --active`. **Parallel catalog ops
  corrupt the catalog** → compose / add one entry at a time.

## Docs

The full, machine-readable index of every CLI command and Python API is at
<https://docs.xorq.dev/llms.txt>.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the source entry plus any
transform entries and/or inline expression to compose, and/or the target catalog
(`-p` / `-n`).
