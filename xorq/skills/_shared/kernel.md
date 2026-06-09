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
   is the ideal; nudge the user to set one.
2. **No default → carry it inline.** You can't `export` across calls (each Bash call is a fresh shell)
   and must **not** `xorq catalog default --set` (machine-global). So **ask the user for a catalog
   name** (suggest the repo name); non-interactively, auto-generate one. `xorq catalog -n <name> init`
   it once, then **prefix every command**: `XORQ_DEFAULT_CATALOG=<name> xorq catalog add …`.
3. **A second / non-default catalog in the same shell → target it explicitly**, before the subcommand:
   `-n <name>` (named) or `-p <path>` (path) — e.g. `xorq catalog -p ./other list`.

Discovery: an existing repo-local catalog counts — glob for `catalog.yaml` (exclude `venv/ .venv/
node_modules/ .git/ __pycache__/ builds*/` and a catalog's own `entries/`/`aliases/`); one hit → use it
(`-p <path>`), several → ask. State `Using catalog: <name | path>` once; don't re-ask.

**Commands below are written bare** (case 1). In case 2, prefix each with `XORQ_DEFAULT_CATALOG=<name>`;
in case 3, add `-n`/`-p`.

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
