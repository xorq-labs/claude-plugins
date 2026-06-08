# xorq — Ambient Context

xorq is a multi-engine data processing framework built on [Ibis](https://ibis-project.org/)
and Apache DataFusion. It writes engine-agnostic, lazy expressions and versions them as
content-addressed artifacts in a git-backed **catalog**.

**Environment (check first):** if `xorq` isn't on PATH (`command -v xorq`), ask the user
(`AskUserQuestion`) whether to make a local uv venv; if they decline, leave the environment
as-is. Install **all three** packages every time — not just the one the current task seems to
need — so the env is the full canonical set regardless of which skill runs next (Python
`>=3.13,<3.14`):

```
uv venv --python 3.13 && \
  uv pip install 'xorq>=0.3.28' 'boring-semantic-layer>=0.3.14' 'scikit-learn>=1.9.0'
```

then call `./.venv/bin/xorq`. Add backend extras (`xorq[duckdb]` etc.) on top as needed.

## Catalog Resolution (do this FIRST, before any catalog operation)

Before any `xorq catalog …` command or `Catalog.from_*` call, resolve **which catalog**
you are operating on. xorq ships with a built-in `default` catalog, so commands work the
moment a user points them at data — but **recommend the user create their own catalog for
their data**. Be explicit about which catalog is in use; don't silently switch between them.

xorq resolves a catalog through **two independent mechanisms**.

### 1. Per-command targeting — `xorq catalog` group flags (preferred)

`-n` / `-p` / `-u` / `-r` are **global flags on the `xorq catalog` group**: they go
**before** the subcommand.

| Flag | Meaning | Resolves to |
|------|---------|-------------|
| `-n, --name <name>` | Named catalog | `~/.local/share/xorq/catalogs/<name>` |
| `-p, --path <dir>` | Catalog at any local path | the repo at `<dir>` (run `init` first if it does not exist) |
| `-u, --url <url>` | Remote catalog (optionally `+ -p <dest>`) | **cloned** from `<url>` (to `<dest>` if given) |
| `-r, --root-repo <dir>` | Submodule install (pair with `-n` or `-u`) | `<dir>/.xorq/catalogs/<name>` |

**How they combine** (verified against the resolver, `Catalog.from_kwargs`): `-n`↔`-p` and
`-n`↔`-u` are mutually exclusive; `-u` may pair with `-p` (the clone destination); `-r`
**requires** exactly one of `-n`/`-u` and **cannot** combine with `-p` (`-r -u <url>` →
clone-as-submodule, `-r -n <name>` → add the named catalog as a submodule).

```bash
xorq catalog -n my-catalog  list --kind       # named, under ~/.local/share/xorq/catalogs/
xorq catalog -p ./my-catalog list --kind      # local path
```

Threading `-p` / `-n` on every call is preferred over relying on the ambient default —
it is explicit, stateless, and survives a fresh shell (each agent `Bash` call starts a new
one — see the default note below). **Exception:** if `xorq catalog default` already reports
a default the *user* set, honor it and drop the flags for that catalog. Only *set* the
default yourself with the user's explicit yes (the onboarding flow below). Python equivalents: `Catalog.from_repo_path(path)`,
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

**Honor a user-set default; set one only when the user says so.** If `xorq catalog default`
reports a name the user set, drop `-p`/`-n` for that catalog. Run `--set` **only** when the
user answers yes to the onboarding question below — it mutates **machine-global, persistent**
state that leaks into every other shell / project / CI run until restored, so never set it
silently or without that explicit yes.

**Why an agent can't just `export` it:** each `Bash` tool call starts a **fresh shell**, so an
inline `export XORQ_DEFAULT_CATALOG=…` is gone by the next call. The env var goes flag-free
across calls only when the **user** sets it where every shell re-sources it (profile / `.envrc`
/ CI env). That's the clean opt-in — the user owns the config; the agent just respects it.

### Resolution procedure

1. **Honor an existing choice first.** If `xorq catalog default` reports a name the *user*
   set, use it flag-free — done. Otherwise glob the repo for `catalog.yaml`, excluding
   `venv/`, `.venv/`, `node_modules/`, `.git/`, `__pycache__/`, `builds*/`, and any catalog's
   own `entries/`/`aliases/`. The parent directory of a surviving hit is a candidate
   (e.g. a hit at `./local_test-catalog/catalog.yaml` → catalog path `./local_test-catalog`).
   - **One candidate** → use it (`-p <path>`), state it, done.
   - **Several** → ask the user which one (`AskUserQuestion`).
2. **No catalog yet — onboard the user (interactive).** Tell them: *xorq ships with a
   built-in `default` catalog, so everything works right now — but we recommend creating your
   own catalog for your data.* Then walk two short questions (`AskUserQuestion`):
   - **"Create a named catalog for your data?"** (recommended) — if yes, pick a name
     (suggest the repo name) and run `xorq catalog -n <name> init`. If no, use the built-in
     `default` (bare commands) and skip the next question.
   - **"Set it as your default?"** — so later commands need no flags. If yes,
     `xorq catalog default --set <name>` (writes `~/.config/xorq/catalog-default`; this is
     machine-global, so only run it on this explicit yes). If no, thread `-n <name>` on each
     command for the rest of the session.
3. **No catalog yet — non-interactive** (`claude -p`, no `AskUserQuestion` tool): you can't
   ask, and `--set`/`init` shouldn't be guessed on the user's behalf — use the built-in
   `default`, state it, and proceed.
4. **Surface the decision.** State plainly `Using catalog: <name | path>`, and don't re-ask
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
