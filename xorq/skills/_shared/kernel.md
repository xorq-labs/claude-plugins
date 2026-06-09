**xorq essentials (shared kernel).** xorq writes engine-agnostic lazy expressions and versions
them as content-addressed artifacts in a git-backed **catalog**. Every skill assumes the vocabulary
below; the plugin's SessionStart hook injects it once per session, so skills don't restate it.

## Environment (check first)

If `xorq` isn't on PATH (`command -v xorq`): interactively, ask before creating a venv;
non-interactively, create one. Install **all three** packages (Python `>=3.13,<3.14`) — not
just the one the task seems to need:

```bash
uv venv --python 3.13 && \
  uv pip install 'xorq>=0.3.28' 'boring-semantic-layer>=0.3.14' 'scikit-learn>=1.9.0'
```

then call `./.venv/bin/xorq`. Add backend extras (`xorq[duckdb]` / `[sqlite]` / `[postgres]`) as needed.

## Catalog resolution (do this FIRST, before any catalog op)

Resolve **which catalog** you operate on, then use the least-verbose correct form:

1. **A default is set → bare commands.** If `xorq catalog default` (read-only) reports a name the
   *user* set (their `XORQ_DEFAULT_CATALOG` / profile), just run `xorq catalog list` — no flags. This
   is the ideal.
2. **No default, but a catalog already exists → adopt it; don't create another.** A just-cloned remote
   (`xorq catalog -u <url>`), a discovered repo-local `catalog.yaml`, or an existing named catalog all
   count. **Surface it and confirm** rather than using it silently: interactively, ask *"Use catalog
   `<name>` as the default for this session?"* (`AskUserQuestion`). If it's path-only / unnamed, ask for
   a name in the same step, or fall back to a sensible default (the repo name). Either way you then
   carry it for the session by **prefixing every command** `XORQ_DEFAULT_CATALOG=<name>` (or `-p <path>`
   for a path-only catalog) — session-only, never `xorq catalog default --set` (machine-global) and you
   can't `export` across Bash calls. Non-interactively, adopt the discovered catalog without asking.
   State `Using catalog: <name | path>` once; don't re-ask.
3. **No default and no catalog exists → create a repo-local one.** Ask for a name (or use the repo
   name); `xorq catalog -p ./<name> init` it once, then pass `-p ./<name>` on each command. Repo-local
   keeps the data with the project, out of global `~/.local/share`. (For a *named* global catalog on
   request instead: `xorq catalog -n <name> init`, then `XORQ_DEFAULT_CATALOG=<name>` / `-n <name>`.)
4. **A second / non-default catalog in the same shell → target it explicitly**, before the subcommand:
   `-n <name>` (named) or `-p <path>` (path) — e.g. `xorq catalog -p ./other list`.

Discovery feeds case 2: glob for `catalog.yaml` (exclude `venv/ .venv/ node_modules/ .git/ __pycache__/
builds*/` and a catalog's own `entries/`/`aliases/`); one hit → that's the existing catalog, several → ask which.

**Commands below are written bare** (case 1). Otherwise prepend the targeting you resolved: `-p ./<name>`
for a repo-local catalog, or `XORQ_DEFAULT_CATALOG=<name>` / `-n <name>` for a named one.

## Primitives

**BUILD-ADD** — mint any entry. Write a script binding the result to `expr`, then build + add:

```bash
xorq build <script.py> --builds-dir builds_<x> --emit-build-path-to bp.txt
xorq catalog add "$(cat bp.txt)" -a <alias>
```

`--builds-dir` isolates the build hash; `--emit-build-path-to` captures the path without parsing
stdout; **add one entry at a time** (git/annex isn't concurrency-safe).

**RECOVER** — re-parameterize a builder entry and run it, persisting nothing:

```bash
xorq catalog run <entry> -c 'source.ls.builder.<method>(<params>)' -o - -f json --limit 5
```

`source` is the entry's expression; `source.ls.builder` recovers the live builder object (polymorphic
over every builder kind); `<method>(<params>)` is that object's own API.

**VERIFY** — confirm a build landed (full read-only vocabulary: the `catalog-explore` skill):

```bash
xorq catalog list --kind            # expect: <hash>  <kind>
xorq catalog schema <alias> --json
xorq catalog run <alias> -o - -f json --limit 5
```

## Shared pitfalls

- **`-o -` is required** for `run` previews — output defaults to `/dev/null`.
- **`VIRTUAL_ENV` mismatch** (`VIRTUAL_ENV=… does not match …`) → prefix commands with `uv run --active`.
- **`--use-this-venv`** runs in the current env (faster, no `uv tool run`) — correct only when it
  already has every dependency the entry needs.
- **One catalog op at a time** — concurrent adds corrupt the catalog.
- **Identical `requirements.txt`** across entries built together — isolated builds merge their bundles.
- **`-c` is sandboxed** to `source` / `xo` / `ibis` (xorq's vendored ibis, `xorq.vendor.ibis`) — no
  imports, no builtins, no dunders.

Docs: full CLI + Python API index at <https://docs.xorq.dev/llms.txt>.
