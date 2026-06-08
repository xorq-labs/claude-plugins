# xorq — Ambient Context

xorq is a multi-engine data processing framework built on [Ibis](https://ibis-project.org/)
and Apache DataFusion. It writes engine-agnostic, lazy expressions and versions them as
content-addressed artifacts in a git-backed **catalog**.

## Catalog Resolution (do this FIRST, before any catalog operation)

Before any `xorq catalog …` command or `Catalog.from_*` call, resolve **which catalog**
you are operating on. Never silently fall through to the built-in `default` — be explicit,
and prefer a repo-scoped catalog.

xorq resolves a catalog through **two independent mechanisms**.

### 1. Per-command targeting — `xorq catalog` group flags (preferred)

`-n` / `-p` / `-u` / `-r` are **global flags on the `xorq catalog` group**: they go
**before** the subcommand.

| Flag | Meaning | Resolves to |
|------|---------|-------------|
| `-p, --path <dir>` | Catalog at any local path | the repo at `<dir>` (run `init` first if it does not exist) |
| `-n, --name <name>` | Named catalog | `~/.local/share/xorq/catalogs/<name>` |
| `-u, --url <url>` | Remote catalog (optionally `+ -p <dest>`) | **cloned** from `<url>` (to `<dest>` if given) |
| `-r, --root-repo <dir>` | Submodule install (pair with `-n` or `-u`) | `<dir>/.xorq/catalogs/<name>` |

**How they combine** (verified against the resolver, `Catalog.from_kwargs`): `-n`↔`-p` and
`-n`↔`-u` are mutually exclusive; `-u` may pair with `-p` (the clone destination); `-r`
**requires** exactly one of `-n`/`-u` and **cannot** combine with `-p` (`-r -u <url>` →
clone-as-submodule, `-r -n <name>` → add the named catalog as a submodule).

```bash
xorq catalog -p ./my-catalog list --kind     # local path
xorq catalog -n my-catalog  list --kind       # named, under ~/.local/share/xorq/catalogs/
```

Threading `-p` / `-n` on every call is preferred over relying on the ambient default —
it is explicit, stateless, and survives a fresh shell (each agent `Bash` call starts a new
one — see the default note below). **Exception:** if `xorq catalog default` already reports
a default the *user* set, honor it and drop the flags for that catalog; never *set* the
default yourself. Python equivalents: `Catalog.from_repo_path(path)`,
`Catalog.from_name(name)`, `Catalog.clone_from(url)`.

If the target does not exist, commands fail with a clear message that names the exact fix —
e.g. `Catalog not found: <path>` … `Run xorq catalog --path <path> init to create it`.
The CLI does **not** auto-create catalogs; you `init` them explicitly (see below).

### 2. The ambient default — when no `-p`/`-n`/`-u` is given

A bare `xorq catalog …` (or `Catalog.from_default()`) resolves the default catalog **name**
with this precedence:

1. **`XORQ_DEFAULT_CATALOG`** environment variable (highest)
2. **`~/.config/xorq/catalog-default`** — the file written by `xorq catalog default --set <name>`
3. built-in **`"default"`** (lowest)

The resolved name maps to `~/.local/share/xorq/catalogs/<name>`. Inspect or change it:

```bash
xorq catalog default                    # read-only: the default's NAME + source -> "my-catalog (source: env (XORQ_DEFAULT_CATALOG))"
xorq catalog default --set my-catalog   # writes ~/.config/xorq/catalog-default — machine-global, persists across sessions
xorq catalog default --unset            # revert to built-in "default"
```

**Honor a user-set default; don't establish one.** If `xorq catalog default` reports a name
the user set, drop `-p`/`-n` for that catalog. Don't run `--set` yourself — it mutates
**machine-global, persistent** state that leaks into every other shell / project / CI run
until restored, the silent fall-through this section opens by warning against.

**Why an agent can't just `export` it:** each `Bash` tool call starts a **fresh shell**, so an
inline `export XORQ_DEFAULT_CATALOG=…` is gone by the next call. The env var goes flag-free
across calls only when the **user** sets it where every shell re-sources it (profile / `.envrc`
/ CI env). That's the clean opt-in — the user owns the config; the agent just respects it.

### Resolution procedure

1. **Look for an existing repo-local catalog.** Glob the repo for `catalog.yaml`, excluding
   `venv/`, `.venv/`, `node_modules/`, `.git/`, `__pycache__/`, `builds*/`, and any catalog's
   own `entries/`/`aliases/`. The parent directory of a surviving hit is a candidate
   (e.g. a hit at `./local_test-catalog/catalog.yaml` → catalog path `./local_test-catalog`).
2. **Decide:**
   - **One candidate** → use it (`-p <path>`).
   - **Several** → ask the user which one (`AskUserQuestion`).
   - **None** → ask the user (`AskUserQuestion`): create a repo-local catalog, or use the
     system default?
     - **Create** (recommended): `xorq catalog -p ./<repo>-catalog init` — the path is
       created and initialized. The `<repo>-catalog` convention keeps repo and catalog names
       aligned so `xorq catalog info` is self-describing.
     - **Use default**: bare commands resolve via the precedence chain above. Tell the user
       which catalog that is (and whether `XORQ_DEFAULT_CATALOG` is set).
3. **Surface the decision.** State plainly `Using catalog: <name | path>`, and don't re-ask
   for the rest of the session.

### Catalog locations

| What | Location |
|------|----------|
| Named catalogs root | `~/.local/share/xorq/catalogs/<name>` |
| Persisted default-name file | `~/.config/xorq/catalog-default` |
| A repo-local catalog | wherever you `init` it with `-p <dir>` (e.g. `./<repo>-catalog`) |

## Building expressions

Every catalog entry bottoms out in one primitive: **`xorq build <script.py>` →
`xorq catalog add <build-path> -a <alias>`**. Rung-1 CLIs (`catalog compose`, the `ingest`
one-liners) run both steps for you. Construct the expression with the lightest tool that
works, and **climb this ladder only as the task demands**:

1. **CLI inline** *(prefer this)* — `xorq catalog compose <src> -c "source.filter(…)"`, or an
   `ingest` `deferred_read_*` one-liner. No script; the CLI builds **and** catalogs.
2. **`expr.*` helpers** *(a small build script, when inline can't express it):*
   - `expr.cache()` (memoize a sub-result), `expr.into_backend(con)` (cross-engine),
     `expr.sql("…")` (raw SQL), `expr.pipe(fn)` (apply a Python transform fn).
   - `expr.unbind()` — generalize a *bound* expr into a reusable **`unbound_expr`** transform;
     the on-ramp from code back to `compose`. (In the `compose` flow itself, binding is
     internal — you never unbind by hand.)
   - Check your work with `expr.ls`: **`expr.ls.kind`** (which `ExprKind` will `catalog add`
     record — `source` / `expr` / `unbound_expr` / `composed` / `expr_builder`?),
     `expr.ls.tokenized` (the content-address hash), `expr.ls.backends` / `is_multiengine`,
     `expr.ls.composed_from` / `builder` / `pipeline(s)` (recover provenance / objects).
3. **From scratch** *(max complexity)* — graph surgery: `expr.ls.fused` / `unwrapped` /
   `untagged` strip catalog/cache wrappers; then `walk_nodes` / `replace_unbound`.

Skills lead with their rung-1 form and point here for anything richer — don't re-teach
`expr.ls` per skill.

The same introspection on a **catalogued entry**, read straight from the git-tracked sidecar
(cheap, always-local — no archive load or annex fetch): `cat.get_catalog_entry(name,
maybe_alias=True)` exposes `e.kind` / `e.columns` / `e.backends` / `e.composed_from` /
`e.is_content_local` / `e.metadata` (`.schema_in` / `.schema_out` / `.params` / `.root_tag`) /
`e.sidecar_metadata`, where `e.expr.ls.*` would load the archive (and fetch annex content).
The CLI mirror — `xorq catalog list / show / schema` — is the **catalog-explore** skill.

## Environment Variables

| Variable | Purpose | Default |
|----------|---------|---------|
| `XORQ_DEFAULT_CATALOG` | Default catalog **name** (resolved under the named-catalogs root) | `default` |
| `XORQ_CACHE_DIR` | Parquet cache directory | `~/.cache/xorq` |
| `XORQ_PROFILE_DIR` | Connection profiles directory | `~/.config/xorq/profiles` |
| `XORQ_DEFAULT_RELATIVE_PATH` | Default relative path for cached data | `parquet` |
| `XORQ_DEBUG` | Verbose debug output | `False` |
| `XORQ_LOG_LEVEL` | File-log level (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`/`OFF`) | `INFO` |
| `XORQ_CATALOG_S3_*`, `XORQ_CATALOG_DIRECTORY_*`, `XORQ_CATALOG_RSYNC_*` | git-annex special-remote config for catalogs whose archives live remotely (consumed by `xorq catalog init --env-prefix … --env-file …` and `embed-readonly`) | — |

> `XORQ_DEFAULT_CATALOG` selects a catalog **name**, not an arbitrary path — for an arbitrary
> path use `-p <dir>` per command. Remote / large-archive storage is configured via the
> `XORQ_CATALOG_*` prefixes together with `xorq catalog init --env-file/--env-prefix`.

> **Docs:** the full, machine-readable index of every CLI command and Python API lives at
> <https://docs.xorq.dev/llms.txt> — consult it for anything not covered here.
