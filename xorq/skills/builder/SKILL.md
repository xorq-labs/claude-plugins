---
description: Round-trip xorq ExprBuilders through the catalog — an expr whose outermost tag carries domain metadata ("an expr that builds exprs"). Recover the live builder object from a catalogued entry to make new exprs, build/originate builder entries, and author custom TagHandlers so your own objects round-trip. Covers semantic models, fitted pipelines, and custom builders generically; for fitting/predicting models use the ml skill.
---

# Builder — Round-Trip ExprBuilders Through the Catalog

An **ExprBuilder** is "an expr that builds exprs": an expression whose **outermost recognized
tag** carries domain metadata. Catalogue one and its kind is **`expr_builder`**; recover the live
domain object from the entry with **`expr.ls.builder`** and call its methods to build new exprs.
This skill is the **round-tripping and extension layer** — to/from builder entries, and how to
make your own objects round-trip. It is **not** an ML tutorial: fitting models lives in **`ml`**;
a fitted pipeline shows up here only as one instance of the machinery.

**One shape, three instances.** Each produces an expr carrying a builder tag; cataloguing it
gives an `expr_builder` entry that round-trips the same way:

| Builder | Produce the tagged expr | `expr.ls.builder` returns | Re-build by calling |
|---|---|---|---|
| Semantic model (boring-semantic-layer) | `model.query(dimensions=…, measures=…).to_tagged()` | the `SemanticModel` | `.query(…)` |
| Fitted pipeline (**`ml`**) | `fitted.predict(<data>)` / `.transform(<data>)` — already tagged | the `FittedPipeline` | `.predict(…)` / `.transform(…)` |
| Custom | `base.tag("<tag>", **metadata)` | your domain object | whatever it exposes |

**Semantic models can span joins.** A model may declare relationships — `join_one` (1:1 /
reference lookup), `join_many` (1:many; pre-aggregates to avoid fan-out), or `join_cross` — with
`on=` (a column name, a `_.col` deferred, a `(l, r) -> bool` lambda, or a list for compound keys)
and `how=`. Joined dimensions and measures are **table-prefixed** — `<table>.<field>` — in both
`.query(...)` and the captured tag metadata, and the cardinality is part of the spec, so a
catalogued joined model runs its stored query across the join and `show` reports the prefixed
fields. Authoring the relationships is boring-semantic-layer's domain (see its docs); for
round-tripping, note one limit: a recovered **`join_one`** model re-queries cleanly via
`.ls.builder`, but re-querying a recovered **`join_many`** model raises a dimension-resolution
error (bsl 0.3.14) — for a different selection over a one-to-many model, re-run the stored query
or rebuild from source (**B**) instead of re-querying the recovered object.

## Resolve the catalog

Run the **Catalog Resolution** procedure in `xorq/CLAUDE.md` first, then thread the target on
every call (`-p <path>` or `-n <name>`) — unless a user-set default already targets it
(`xorq catalog default`), then drop the flags. Below uses `CAT=<catalog>`.

## A. CLI-first — recover a builder entry and re-query it

A bare `run` executes the entry's stored query; with `-c` you **recover the builder and
re-parameterize it** — "use an expr builder to make a new expr" — and execute, without writing a
new entry. **`-o -` is required** (output defaults to `/dev/null`):

```bash
CAT=<catalog>
xorq catalog -p "$CAT" run <builder-entry> -o - -f json --limit 5          # the stored query

xorq catalog -p "$CAT" run <builder-entry> \
  -c 'source.ls.builder.<method>(<params>).to_tagged()' \
  -o - -f json --limit 5                                                     # recover + re-query
```

- `source` is the entry's expression; `source.ls.builder` recovers the live domain object.
- `<method>(<params>)` is **that object's own API** — `.query(dimensions=[…], measures=[…])` for a
  semantic model, `.predict(<data>)` / `.transform(<data>)` for a fitted pipeline, or whatever a
  custom builder exposes.
- `.to_tagged()` re-tags a semantic-model query result so the new expr is itself a builder; a
  fitted-pipeline / custom expr is already tagged, so it's often unnecessary.
- The `-c` namespace is **sandboxed** to `source` / `xo` / `ibis` (xorq's vendored ibis,
  `xorq.vendor.ibis`) — no imports, no builtins, no dunder access. Inspect a builder entry's type and metadata with `xorq catalog show <entry>`
  (the read-only inspection vocabulary is the **`catalog-explore`** skill).

To **persist** a re-parameterized builder as its own `expr_builder` entry, use the build path
(**B**) — `catalog compose` always records a `composed` entry (see **`composer`**), even when its
`-c` ends in `.to_tagged()` (the compose wrapper is outermost). The builder stays recoverable
under that wrapper via `.ls.builder`, but the entry kind is `composed`, not `expr_builder`.

## B. Build / originate a builder entry from a script

When the builder isn't catalogued yet, mint it with the same **build → add** primitive every
entry uses (the lightest-tool ladder and `expr.ls.*` introspection are ambient — see **Building
expressions** in `xorq/CLAUDE.md`; the add step mirrors **`composer`** / **`ingest`**). The only
new thing is producing a **tagged** expression:

```python
# build_builder.py — bind the result expression to `expr`
import xorq.api as xo
# construct the builder over a source (a deferred read, or a catalogued expr), then:
expr = <builder-result>
#   semantic model : model.query(dimensions=(…,), measures=(…,)).to_tagged()
#   fitted pipeline: fitted.predict(<data>)            # already builder-tagged
#   custom         : base.tag("<tag>", **metadata)     # needs a registered handler (C)
```

```bash
xorq build build_builder.py --builds-dir builds_builder --emit-build-path-to bp.txt
xorq catalog -p "$CAT" add "$(cat bp.txt)" -a <alias>      # kind: expr_builder
```

## C. Author a custom TagHandler (the extension layer)

Make any domain object round-trip: declare a **`TagHandler`** that maps your tag to (1) sidecar
metadata and (2) a recovered object, then **`tag`** your expressions with it.

```python
# my_handlers.py
from xorq.expr.builders import TagHandler, register_tag_handler

def _extract_metadata(tag_node):              # -> sidecar dict; always readable via `catalog show`
    m = tag_node.metadata                     # the kwargs you passed to .tag(...), plus "tag"
    return {"type": "<tag>", "description": "<summary>", **{k: m.get(k) for k in (...)}}

def _from_tag_node(tag_node):                 # -> the live object; used by expr.ls.builder
    base = tag_node.parent.to_expr()          # the expression directly below the tag
    m = tag_node.metadata
    return MyBuilder.from_parts(base, m)       # reconstruct from what the tag captured

handler = TagHandler(
    tag_names=("<tag>",),
    extract_metadata=_extract_metadata,        # at least one of extract_metadata / from_tag_node
    from_tag_node=_from_tag_node,
    # reemit=...,                              # opt-in: rebuild hook for `catalog replay --rebuild`
)
register_tag_handler(handler)                  # in-process registration — see Pitfalls
```

Tag an expression to make it a builder: `base.tag("<tag>", key=value, …)`.

For the handler to be found across **separate processes** (`build` / `catalog add` / `catalog
run` each run in their own, often isolated, env), **register it as an entry point** in the
package that ships it — not just in-process:

```toml
# pyproject.toml of the package that defines the handler
[project.entry-points."xorq.from_tag_node"]
my_plugin = "my_package.my_handlers:handler"
```

Built-in handlers (the ML `FittedPipeline`) and entry-point handlers (e.g. boring-semantic-layer
ships one) are discovered automatically in every process.

## Verify

```bash
xorq catalog -p "$CAT" list --kind        # expect: <hash>  expr_builder
xorq catalog -p "$CAT" show <alias>       # "Type: Expression Builder", "Root tag:", a "Builders:" block
xorq catalog -p "$CAT" run <alias> -c 'source.ls.builder.<method>(<params>).to_tagged()' \
  -o - -f json --limit 5                  # round-trips: recovers the builder and re-queries
```

`expr.ls.expr_traits.has_builders` is a cheap in-process predicate. Full inspection vocabulary
(`show` / `schema` / `list`) is the **`catalog-explore`** skill.

## Pitfalls

- **Outermost-tag-only detection.** The kind is decided by the **outermost recognized** builder
  tag. An **unrecognized** tag is decorative — the entry classifies as its underlying kind
  (`source` / `expr`). Wrapping a builder (e.g. via `catalog compose`) yields a `composed` entry,
  not `expr_builder`; the builder is still recoverable underneath via `.ls.builder`. To mint an
  `expr_builder` entry, use the build path (**B**).
- **Registration scope is process-local.** `register_tag_handler(...)` lasts only for the current
  Python process. `build` / `catalog add` / `catalog run` are **separate** processes, so a custom
  handler must be installed as a **`xorq.from_tag_node` entry point** in the build's environment —
  otherwise `.ls.builder` raises `No builder tags found in expression`. The **sidecar**
  (`extract_metadata`) is captured at add time and always reads back; only **live recovery**
  (`from_tag_node`) needs the handler present.
- **`from_tag_node` reconstructs only what the tag captured.** Store everything the rebuild needs
  in the `.tag(...)` metadata — the recovered object can't see fields that weren't tagged.
- **Tag-key collisions.** A duplicate `tag_names` registration raises (pass `override=True` to
  replace); built-in tag keys are protected and can't be overridden.
- **`-c` is sandboxed** to `source` / `xo` / `ibis` — no imports, no builtins, no dunders.
- **Identical `requirements.txt`** across entries built together (isolated builds merge bundles);
  **one catalog op at a time** (git/annex isn't concurrency-safe); **`VIRTUAL_ENV` mismatch** →
  prefix with `uv run --active`.

## Docs

The full, machine-readable index of every CLI command and Python API is at
<https://docs.xorq.dev/llms.txt>.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the builder entry to round-trip
(recover + re-query, or persist a re-parameterized variant), and/or the target catalog
(`-p` / `-n`).
