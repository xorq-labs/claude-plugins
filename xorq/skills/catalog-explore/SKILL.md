---
description: Find and inspect xorq catalogs read-only — discover which catalogs exist, list entries and aliases, show metadata and schemas, preview rows, read history. No build, no acquire, no add. Also the verification vocabulary for confirming a build produced the right entry.
---

# Catalog-Explore — Read-Only Catalog Inspection

Discover which catalogs exist, then look inside one: list entries and aliases, show an entry's metadata
and schema, preview rows, read history. Everything here is **read-only** — it never changes the catalog.
This is also the **verification vocabulary** the other skills lean on (the kernel's VERIFY points here):
after an `ingest` / `composer` / `ml` build, confirm the result with `list --kind`, `schema`, `show`,
and a `run` preview.

**Read-only commands only:** `info` · `list` · `list-aliases` · `show` · `schema` · `log` · `check` ·
`default` (no flags) · `run` (executes, adds nothing). **Never** `add` / `remove` / `add-alias` /
`remove-alias` / `compose` / `init` / `clone` / `pull` / `push` / `sync` / `set-remote` — those mutate
(to create entries use `ingest` / `composer` / `ml`). The kernel's BUILD-ADD / RECOVER are for those
skills; here, only read.


## 1. Find a catalog

Resolution (which catalog; the flags / env var) is in the kernel. To see what you're pointed at:

```bash
xorq catalog default   # the default's NAME and source -> "default  (source: built-in)"
xorq catalog info      # resolves it, prints the PATH (+ commit, remotes, entry/alias counts)
```

Target a specific one with a group flag (`-p <path> info`, `-n <name> info`) or `XORQ_DEFAULT_CATALOG=<name> … info`.

## 2. List entries and aliases

```bash
xorq catalog list --kind             # entries (content hashes) + kind
xorq catalog list-aliases            # the human-readable handles
```

**Entries are content hashes; aliases are the names that point at them.** `list` shows hashes,
`list-aliases` shows handles — `show` / `schema` / `run` accept **either**.

## 3. Inspect one entry

```bash
xorq catalog show <alias>            # full metadata: kind, aliases, backends, content-local, composed-from / params / builders, in/out schema
xorq catalog schema <alias> --json   # ExprMetadata as JSON (schema_in/out, params…)
xorq catalog show <alias> --raw      # the metadata sidecar verbatim (YAML)
```

## 4. Preview rows (read-only execution)

`run` composes-and-executes an entry without persisting (`-o -`, per kernel):

```bash
xorq catalog run <alias> -o - -f json --limit 5   # one JSON object per row
```

## 5. History & integrity

```bash
xorq catalog log [--json]   # replay plan: [init]/[add]/[remove] ops + summary
xorq catalog check          # validate consistency -> "OK"
```

(Interactive browsing for humans, not headless agents: `xorq catalog tui`.)

## Verify (the VERIFY vocabulary)

The canonical post-build checks the other skills point back to:

```bash
xorq catalog list --kind                          # expect the new <hash>  <kind>
xorq catalog list-aliases                         # expect your alias present
xorq catalog schema <alias> --json                # expect the right schema_out
xorq catalog run <alias> -o - -f json --limit 5   # expect rows
```

The Python mirror (`cat.get_catalog_entry(...)` + sidecar `.kind` / `.columns` / `.schema_out`) is in
[reference.md](../_shared/reference.md).

## Pitfalls (explore-specific; shared ones are in the kernel)

- **No auto-create.** Read commands never initialize a catalog; a missing target fails with a clear
  "Catalog not found … run `init`" message.
- **Hash vs alias.** Pass either to `show` / `schema` / `run`; "Entry not found" → check both lists.
- **Content may not be local.** A cloned catalog can show `Content local: no` — sidecar metadata
  (kind / schema / backends / aliases) still reads, but previewing rows or loading `.expr` triggers an
  annex fetch from the remote.

## Arguments

`$ARGUMENTS`: the entry or alias to inspect, and/or the catalog to explore (`-p` / `-n`).
