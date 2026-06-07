---
description: Set up and populate a xorq catalog WITHOUT building — create a new catalog, clone or submodule an existing one (-u / -r), pull updates, and copy entries between catalogs (replay, get + add). Use when onboarding to a shared catalog or vendoring one into a repo.
---

# Init — Set Up & Populate a Catalog (no build)

A xorq catalog is a **git + git-annex repo**. Entries are content-addressed build
artifacts that are built **once** and then distributed git-style. This skill is the
**no-build** path: create a catalog, acquire an existing one, keep it in sync, and copy
entries between catalogs — none of which rebuilds anything.

> **Need to originate a net-new `Source`** from raw data (a `.csv`/`.parquet` file or a
> DuckDB/SQLite/Postgres table) that exists in **no** catalog yet? That's the one
> operation that requires a build — see the **`ingest`** skill (`deferred_read_*` →
> `xorq build` → `xorq catalog add`).

## Resolve / target the catalog

Run the **Catalog Resolution** procedure in `xorq/CLAUDE.md` first. The `-n / -p / -u / -r`
selectors are **global flags on the `xorq catalog` group** — they come **before** the
subcommand. How they combine (verified against the resolver, `Catalog.from_kwargs`):

| Flags | Resolves to |
|-------|-------------|
| `-p <dir>` | catalog repo at `<dir>` |
| `-n <name>` | named catalog under `~/.local/share/xorq/catalogs/<name>` |
| `-u <url>` *(opt. `+ -p <dest>`)* | **clone** the remote catalog (to `<dest>` if given) |
| `-r <root> -u <url>` | **clone as submodule** at `<root>/.xorq/catalogs/<name>` |
| `-r <root> -n <name>` | add the **named** catalog as a submodule under `<root>` |

`-n`↔`-p` and `-n`↔`-u` are mutually exclusive; `-r` **requires** exactly one of
`-n`/`-u` and **cannot** combine with `-p`.

## Acquire an existing catalog (no build)

The entries were built by whoever published the catalog; you just fetch them.

**Clone** a remote catalog — get all its entries:
```bash
xorq catalog clone <url> -p ./<repo>-catalog      # or: -n <name>
xorq catalog -u <url> -p ./<repo>-catalog list --kind   # equivalent via group flag
```

**Submodule** — vendor a catalog into your repo, pinned to a commit:
```bash
xorq catalog -r <repo-root> -u <url> info         # clone-as-submodule
xorq catalog -r <repo-root> -n <name> info        # named catalog as submodule
# lands at <repo-root>/.xorq/catalogs/<name>
```

**Pull** newer entries from the configured remote (and `sync` = pull then push):
```bash
xorq catalog -p "$CAT" pull
xorq catalog -p "$CAT" sync
```

## Create a new (empty) catalog

```bash
xorq catalog -p ./<repo>-catalog init
```
Optionally wire a git remote and git-annex archive storage up front (the archives — the
heavy build artifacts — live in a git-annex special remote, e.g. S3/GCS):
```bash
xorq catalog -p ./<repo>-catalog init \
  --remote-url <git-url> \
  --env-prefix XORQ_CATALOG_S3_ --env-file .env.catalog.s3      # add --gcs for GCS
```
Set or replace the git remote later (a catalog has **at most one** remote, ADR-0011 —
`set-remote` refuses to overwrite without `--force`):
```bash
xorq catalog -p "$CAT" set-remote <git-url>          # --force to replace
```

## Copy entries between catalogs (no rebuild)

**Replay** — copy entries from a source catalog into a target:
```bash
xorq catalog -p <src> replay <target-path>             # copy as-is, no rebuild
xorq catalog -p <src> replay <target-path> --rebuild   # re-add under current code
xorq catalog -p <src> replay <target-path> --dry-run   # preview only
# --remote-url sets the target's origin and pushes
```

**Transfer a single entry** — export its built archive, add it elsewhere (an
already-built archive is registered as-is; no rebuild). `get` takes the entry **hash**
(from `list`, **not** an alias) and writes `<hash>.zip` into an **existing** directory:
```bash
HASH=$(xorq catalog -p <src> list --kind | awk '$2=="source"{print $1; exit}')
mkdir -p /tmp/x
xorq catalog -p <src> get "$HASH" -o /tmp/x          # -> /tmp/x/<hash>.zip
xorq catalog -p <dst> add "/tmp/x/$HASH.zip" -a <alias>
```

## Verify

```bash
xorq catalog -p "$CAT" info                  # path, remotes, entry/alias counts
xorq catalog -p "$CAT" list --kind
xorq catalog -p "$CAT" list-aliases
xorq catalog -p "$CAT" log --json            # history as structured operations
xorq catalog -p "$CAT" show <entry|alias>    # full metadata
xorq catalog -p "$CAT" schema <alias> --json
```

Preview an acquired entry's rows — **`-o -` is required** (output defaults to `/dev/null`):
```bash
xorq catalog -p "$CAT" run <alias> -o - -f json --limit 5
```
`catalog run` reconstructs the entry's pinned environment in an isolated `uv tool run`;
add `--use-this-venv` to run in the current environment when it already has the drivers.

## Pitfalls

- **Archives live in git-annex.** `clone`/`pull` fetch git metadata + annex pointers;
  fetching the actual build archives needs the catalog's annex remote configured (the
  `XORQ_CATALOG_*` env prefixes). For read-only sharing, the publisher embeds creds with
  `xorq catalog embed-readonly --env-prefix … --env-file …` so consumers can fetch.
- **One git remote per catalog** (ADR-0011) → `set-remote` refuses to overwrite without
  `--force`.
- **`-r` needs a partner** → pair it with `-n` or `-u`, never `-p`.
- **Parallel ops corrupt the catalog** → `add`/`get`/`replay` one entry/catalog at a time.
- **`VIRTUAL_ENV` mismatch** (`VIRTUAL_ENV=… does not match …`) → prefix commands with
  `uv run --active`.

## Docs

The full, machine-readable index of every CLI command and Python API is at
<https://docs.xorq.dev/llms.txt>.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as a catalog URL/path/name to
acquire or create (`-u`/`-p`/`-n`, `-r` for a submodule), or entries to copy.
