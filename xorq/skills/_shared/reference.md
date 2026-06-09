# xorq reference (depth — read on demand)

Background the [kernel](kernel.md) doesn't carry. Read this only when a task needs the detail.

## Catalog targeting flags

`-n` / `-p` / `-u` / `-r` are **global flags on the `xorq catalog` group** — they go **before** the
subcommand.

| Flag | Meaning | Resolves to |
|------|---------|-------------|
| `-n, --name <name>` | Named catalog | `~/.local/share/xorq/catalogs/<name>` |
| `-p, --path <dir>` | Catalog at any local path | the repo at `<dir>` (run `init` first if it doesn't exist) |
| `-u, --url <url>` | Remote catalog (optionally `+ -p <dest>`) | **cloned** from `<url>` (to `<dest>` if given) |
| `-r, --root-repo <dir>` | Submodule install (pair with `-n` or `-u`) | `<dir>/.xorq/catalogs/<name>` |

**How they combine** (`Catalog.from_kwargs`): `-n`↔`-p` and `-n`↔`-u` are mutually exclusive; `-u`
may pair with `-p` (clone destination); `-r` **requires** exactly one of `-n`/`-u` and **cannot**
combine with `-p`. Python equivalents: `Catalog.from_repo_path(path)`, `Catalog.from_name(name)`,
`Catalog.clone_from(url)`. Threading `-p`/`-n` per call is preferred over the ambient default — it's
explicit, stateless, and survives a fresh shell (each `Bash` call starts a new one). Missing targets
fail with a message that names the fix (`Run xorq catalog --path <path> init to create it`); the CLI
never auto-creates.

## The ambient default — when no `-p`/`-n` is given

A bare `xorq catalog …` (or `Catalog.from_default()`) resolves the default **name** by precedence:

1. **`XORQ_DEFAULT_CATALOG`** environment variable (highest)
2. **`~/.config/xorq/catalog-default`** — a persisted default-name file, if the user set one
3. built-in **`"default"`** (lowest)

```bash
xorq catalog default                    # read-only: the default's NAME + source
```

**Inline the env var; don't persist a default.** To target a named catalog without per-command `-n`,
prefix the command — `XORQ_DEFAULT_CATALOG=<name> xorq catalog list`. Inline it on **every** command:
don't `export` it (each `Bash` call is a fresh shell, so an export is gone next call), and don't
`xorq catalog default --set` it (that writes the machine-global, persistent file above, leaking into
every other shell / project / CI run). If the *user* has already set a default (their env or persisted
file), honor it with bare commands; otherwise carry the catalog explicitly on each command.

## Onboarding (interactive) — adopt an existing catalog, or create one

**If a catalog already exists** (the user just cloned a remote with `xorq catalog -u <url>`, there's a
repo-local `catalog.yaml`, or a named catalog is present), don't silently use it and don't create a
second one — **confirm and adopt it as the session default**. One `AskUserQuestion`:

- **"Use catalog `<name>` as the default for this session?"** (recommended) — if yes, carry it per
  command with `XORQ_DEFAULT_CATALOG=<name>` (or `-p <path>` if it's path-only). If it has no name
  (path-only), ask for one in the same step or fall back to the repo name. If no, keep targeting it
  explicitly per command with `-p`/`-n` and don't make it the session default.

**If no catalog exists**, tell them: *xorq ships with a built-in `default` catalog, so everything works
now — but we recommend creating your own for your data.* Ask one `AskUserQuestion`:

- **"Create a named catalog for your data?"** (recommended) — if yes, pick a name (suggest the repo
  name) and `xorq catalog -n <name> init`, then carry it per command with `XORQ_DEFAULT_CATALOG=<name>`
  (or `-n <name>`). If they'd rather not have a global named catalog, create it **repo-local** instead:
  `xorq catalog -p ./<name> init`, then pass `-p ./<name>` per command (keeps the data with the
  project, out of `~/.local/share`). If no catalog at all, use the built-in `default`.

A named catalog's default is **session-only** via `XORQ_DEFAULT_CATALOG=<name>` per command — never
persist with `xorq catalog default --set` (machine-global, leaks into every other shell/project/CI);
a repo-local one is targeted with `-p ./<name>`. Non-interactive (`claude -p`, no `AskUserQuestion`):
adopt the existing catalog if one is present, else create a named one — `xorq catalog -n <name> init`
(repo name) — and prefix commands with `XORQ_DEFAULT_CATALOG=<name>`; state it.

## Catalog locations

| What | Location |
|------|----------|
| Named catalogs root | `~/.local/share/xorq/catalogs/<name>` |
| Persisted default-name file | `~/.config/xorq/catalog-default` |
| A repo-local catalog | wherever you `init` it with `-p <dir>` (e.g. `./<repo>-catalog`) |

## Building expressions — the lightest-tool ladder

Every entry bottoms out in **BUILD-ADD** (see kernel). Construct the expression with the lightest tool
that works, and climb only as the task demands:

1. **CLI inline** *(prefer)* — `xorq catalog compose <src> -c "source.filter(…)"`, or an `ingest`
   `deferred_read_*` one-liner. No script; the CLI builds **and** catalogs.
2. **`expr.*` helpers** *(a small build script):* `expr.cache()` (memoize), `expr.into_backend(con)`
   (cross-engine), `expr.sql("…")` (raw SQL), `expr.pipe(fn)` (apply a Python transform);
   `expr.unbind()` generalizes a bound expr into a reusable **`unbound_expr`** transform (the on-ramp
   back to `compose`).
3. **From scratch** *(max complexity)* — graph surgery: `expr.ls.fused` / `unwrapped` / `untagged`
   strip catalog/cache wrappers, then `walk_nodes` / `replace_unbound`.

## Introspection

Check an expression with **`expr.ls`**: `expr.ls.kind` (which `ExprKind` `catalog add` will record —
`source` / `expr` / `unbound_expr` / `composed` / `expr_builder`), `expr.ls.tokenized` (content hash),
`expr.ls.backends` / `is_multiengine`, `expr.ls.composed_from` / `builder` / `pipeline(s)` (recover
provenance / objects), `expr.ls.expr_traits.has_builders`.

The same on a **catalogued entry**, read from the git-tracked sidecar (cheap, always-local — no archive
load or annex fetch): `cat.get_catalog_entry(name, maybe_alias=True)` exposes `e.kind` / `e.columns` /
`e.backends` / `e.composed_from` / `e.is_content_local` / `e.metadata` (`.schema_in` / `.schema_out` /
`.params` / `.root_tag`) / `e.sidecar_metadata`. `e.expr.ls.*` would load the archive (and fetch annex
content). The CLI mirror — `xorq catalog list / show / schema` — is the **catalog-explore** skill.

## Environment variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `XORQ_DEFAULT_CATALOG` | Default catalog **name** (under the named-catalogs root) | `default` |
| `XORQ_CACHE_DIR` | Parquet cache directory | `~/.cache/xorq` |
| `XORQ_PROFILE_DIR` | Connection profiles directory | `~/.config/xorq/profiles` |
| `XORQ_DEFAULT_RELATIVE_PATH` | Default relative path for cached data | `parquet` |
| `XORQ_DEBUG` | Verbose debug output | `False` |
| `XORQ_LOG_LEVEL` | File-log level (`DEBUG`…`CRITICAL`/`OFF`) | `INFO` |
| `XORQ_CATALOG_S3_*`, `XORQ_CATALOG_DIRECTORY_*`, `XORQ_CATALOG_RSYNC_*` | git-annex special-remote config for catalogs with remote archives (`xorq catalog init --env-prefix … --env-file …`, `embed-readonly`) | — |

`XORQ_DEFAULT_CATALOG` selects a **name**, not an arbitrary path — for a path use `-p <dir>` per command.
