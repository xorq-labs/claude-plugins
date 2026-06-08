---
description: Find and inspect xorq catalogs read-only — discover which catalogs exist, list entries and aliases, show metadata and schemas, preview rows, read history. No build, no acquire, no add. Also the verification vocabulary for confirming a build produced the right entry.
---

# Catalog-Explore — Read-Only Catalog Inspection

Discover which catalogs exist, then look inside one: list entries and aliases, show an
entry's metadata and schema, preview a few rows, read the history. Everything here is
**read-only** — it never changes the catalog. This is also the **verification vocabulary**
the other skills lean on: after an `ingest` / `composer` / `ml` build, you confirm the
result with `list --kind`, `schema`, `show`, and a `run` row-preview from here.

> **Read-only only.** This skill runs only commands that cannot mutate a catalog:
> `info` · `list` · `list-aliases` · `show` · `schema` · `log` · `check` · `default` (no
> flags) · `run` (executes, adds nothing). It does **not** `add` / `remove` /
> `add-alias` / `remove-alias` / `compose` / `init` / `clone` / `pull` / `push` / `sync` /
> `set-remote` — those mutate. To create entries use `ingest` / `composer` / `ml`; to
> acquire a catalog (clone/pull/replay) or **set** the default (`default --set`) see
> `xorq/CLAUDE.md` — that's xorq's own machinery, not this skill.

## 1. Find a catalog

Catalog *resolution* (the `-p`/`-n`/`-u`/`-r` flags, the default precedence) is ambient — see
**`xorq/CLAUDE.md` → Catalog Resolution**. To see what you're pointed at, ask xorq rather than
hardcoding paths:

```bash
xorq catalog default   # the default catalog's NAME and why -> "default  (source: built-in)"
xorq catalog info      # resolves it and prints its PATH (+ commit, remotes, entry/alias counts)
```

`info` is the clean way to get a catalog's path; target a specific one with a group flag
(`xorq catalog -n <name> info`, `-p <path> info`). To find catalogs you haven't named yet:

- **Repo-local:** glob the repo for `catalog.yaml` (exact glob + exclusions in CLAUDE.md's
  resolution procedure); the parent dir of a hit is the catalog path.
- **All named catalogs:** xorq has no command that enumerates them — they live under its store
  root `~/.local/share/xorq/catalogs/` (HOME-relative), so `ls` that only when you need the list.

Then thread the chosen target on **every** command — `-p <path>` or `-n <name>`, before the
subcommand. Below uses `CAT=<catalog>`. **Exception:** if `xorq catalog default` already
reports a default the user set, honor it and drop the flags (see CLAUDE.md → Catalog
Resolution). Read commands never auto-create: a missing target fails with a message that
names the fix.

## 2. List what's inside

```bash
CAT=<catalog>                                  # e.g. ./my-catalog   (or: -n my-catalog)
xorq catalog -p "$CAT" info                    # path, commit, remotes, entry/alias counts
xorq catalog -p "$CAT" list --kind             # entries (content hashes) + kind
xorq catalog -p "$CAT" list-aliases            # the human-readable handles
```
```
# list --kind                 # list-aliases
a1b2c3d4e5f6	source          <your-alias>
```

**Entries are content hashes; aliases are the human names that point at them.** `list`
shows hashes, `list-aliases` shows handles — and `show` / `schema` / `run` accept **either**.

## 3. Inspect one entry

```bash
xorq catalog -p "$CAT" show <alias>            # full metadata (by alias or hash)
xorq catalog -p "$CAT" schema <alias>          # just the schema
xorq catalog -p "$CAT" schema <alias> --json   # ExprMetadata as JSON (schema_in/out, params…)
xorq catalog -p "$CAT" show <alias> --raw      # the metadata sidecar verbatim (YAML)
```

`show` reports the kind, aliases, backends, content-local flag, composed-from / params /
builders when present, and the in/out schema:

```
Name:           a1b2c3d4e5f6
Aliases:        <alias>
Type:           Source (bound)
Backends:       <backend>
Content local:  yes
Schema Out:
  <col>                    <dtype>
  …
```

## 4. Preview rows (read-only execution)

`run` composes-and-executes an entry without persisting anything. **`-o -` is required** —
output defaults to `/dev/null`:

```bash
xorq catalog -p "$CAT" run <alias> -o - -f json --limit 5
# one JSON object per row: {"<col>": <value>, ...}
```

- `--use-this-venv` runs in the current environment (faster, no `uv tool run` subprocess) —
  correct only when this env already has every dependency the entry needs.
- **DuckDB caveat (0.3.28):** `catalog run` can't locate a materialized DuckDB entry's
  parquet — preview those with `xorq run <build-path>` instead (see `ingest`).

## 5. Read history & check integrity

```bash
xorq catalog -p "$CAT" log          # replay plan: [init]/[add]/[remove] ops + summary
xorq catalog -p "$CAT" log --json   # same, structured
xorq catalog -p "$CAT" check        # validate catalog consistency -> "OK"
```

Interactive browsing (humans, not headless agents): `xorq catalog -p "$CAT" tui`.

## Verifying a build produced the right entry

The canonical post-build checks the other skills point back to:

```bash
xorq catalog -p "$CAT" list --kind                       # expect the new <hash>  <kind>
xorq catalog -p "$CAT" list-aliases                      # expect your alias present
xorq catalog -p "$CAT" schema <alias> --json             # expect the right schema_out
xorq catalog -p "$CAT" run <alias> -o - -f json --limit 5  # expect rows
```

## Inline Python

Prefer the CLI above. The Python equivalent — `cat.get_catalog_entry(name, maybe_alias=True)`
and an entry's sidecar-backed `.kind` / `.columns` / `.schema_out` / … properties — is
ambient: see **Building expressions** in `xorq/CLAUDE.md`.

## Pitfalls

- **No auto-create.** Read commands never initialize a catalog; a missing target fails with
  a clear "Catalog not found … run `init`" message. Resolve the catalog first (CLAUDE.md).
- **Hash vs alias.** `list` prints content hashes, `list-aliases` prints handles; pass
  either to `show` / `schema` / `run`. "Entry not found" → check both lists.
- **Content may not be local.** A cloned catalog can show `Content local: no` — sidecar
  metadata (kind/schema/backends/aliases) still reads, but previewing rows or loading
  `.expr` triggers an annex fetch from the remote.
- **`-o -` for previews** — without it `run` writes to `/dev/null` and you see nothing.
- **`VIRTUAL_ENV` mismatch** (`VIRTUAL_ENV=… does not match …`) → prefix with
  `uv run --active`.

## Docs

The full, machine-readable index of every CLI command and Python API is at
<https://docs.xorq.dev/llms.txt>.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the catalog to explore
(`-p` / `-n`) and/or the entry or alias to inspect.
