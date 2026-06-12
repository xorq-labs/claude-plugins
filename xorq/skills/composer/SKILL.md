---
description: Compose a new catalog entry from data already in the catalog — apply reusable transform entries and/or an inline xorq expression to a source entry, building and cataloguing the result in one step (kind "composed"). Use when the inputs already live in a catalog; for raw files/tables use ingest, for fitting models use ml.
---

# Composer — Compose a New Entry from Catalogued Expressions

Take a **source** entry and shape it — with an inline expression, reusable **transform** entries, or
both — into a new content-addressed **`composed`** entry. One command **builds and catalogs** (no
separate `catalog add`):

```bash
xorq catalog compose <source> [<transform> …] [-c "<inline expr>"] -a <alias>
```

- **First entry = the source** — the base (any data-yielding kind: `source` / `composed` / `expr`).
- **Remaining entries = transforms**, applied in order; each must be an **`unbound_expr`** entry (B).
- **`-c` / `--code`** = an inline xorq expression over the variable `source`. Transforms and `--code`
  combine; **code runs last**. The result is always a new `composed` entry; `-a` adds an alias.


## A. Inline code — ad-hoc, most general

```bash
xorq catalog compose <source> \
  -c "source.filter(source.<col> > <n>).select('<col>', '<other_col>')" -a <alias>
```

Reference columns as `source.<col>` or `source['<col>']`; chain xorq ops freely. The `-c` namespace is
sandboxed (see essentials) — for anything heavier than an expression chain, make a transform (B).

## B. Reusable transform entries — versioned, schema-checked

A transform is an expression over an **unbound** table (schema only, no data), so it reapplies to any
compatible source. Built over `xo.table(...)`, it catalogs as **`unbound_expr`** (input = `schema_in`,
output = `schema_out`):

```python
# transform.py — data-agnostic; binds to a source at compose time
import xorq.api as xo
t = xo.table({"value": "float64", "label": "string"}, name="t")   # unbound: schema only
expr = t.filter(t.value > 0).group_by("label").agg(total=t.value.sum())
```

BUILD-ADD it (`-a <name>` → kind `unbound_expr`), then apply it (chain several by listing more entries):

```bash
xorq catalog compose <source> <transform> -a <alias>
```

**Schema rule:** the source's columns must be a **superset** of the transform's `schema_in` (matched by
name + type); chained transforms validate left-to-right and a mismatch lists the offending columns.

## Joining two entries

`compose` is **single-input** — its `--code` sees only `source`. To **join two catalog entries**, drop
to a build script (load each entry's expression, join, then BUILD-ADD) — the result is an `expr` entry,
not `composed`. See the lightest-tool ladder in [reference.md](../_shared/reference.md).

## Verify

Run **VERIFY** (essentials). Expect kind **`composed`**; `catalog show <alias>` reports "Composed from: N".
`--dry-run` previews the plan + result schema without building; `compose` otherwise always catalogs
(to execute without an entry, use `xorq catalog run`).

## Pitfalls (composer-specific; shared ones are in the essentials)

- **Transforms must be `unbound_expr`** (built over `xo.table(schema=…)`, not real data). The CLI
  does **not** reject a data-bearing (`source` / `composed`) entry passed as a transform — it
  composes silently and contributes nothing (0.3.29), so the mistake surfaces only as missing
  transformation in the result.
- **Always catalogs** — no build-only mode; use `--dry-run` to preview or `catalog run` to just execute.
- **`--rename-params entry,old,new`** resolves an unbound-parameter name clash on a specific entry
  (repeatable); only needed when chained transforms collide on a param name.

## Arguments

`$ARGUMENTS`: the source entry plus any transform entries / inline expression, and/or the target
catalog (`-p` / `-n`).
