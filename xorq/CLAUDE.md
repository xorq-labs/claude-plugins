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
it is explicit and survives subshells. Python equivalents: `Catalog.from_repo_path(path)`,
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
xorq catalog default                    # e.g. "my-catalog  (source: env (XORQ_DEFAULT_CATALOG))"
xorq catalog default --set my-catalog   # persist a sticky default
xorq catalog default --unset            # revert to built-in "default"
```

**Clever env-var setup:** export `XORQ_DEFAULT_CATALOG=<name>` once (shell profile, `.envrc`,
CI env) and every bare `xorq catalog` command in that session targets it — no `-p`/`-n` needed.

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
