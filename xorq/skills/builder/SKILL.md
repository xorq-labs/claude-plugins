---
description: Round-trip xorq ExprBuilders through the catalog — an expr whose outermost tag carries domain metadata ("an expr that builds exprs"). Recover the live builder object from a catalogued entry to make new exprs, build/originate builder entries, and author custom TagHandlers so your own objects round-trip. Covers semantic models, fitted pipelines, and custom builders generically; for fitting/predicting models use the ml skill.
---

# Builder — Round-Trip ExprBuilders Through the Catalog

An **ExprBuilder** is "an expr that builds exprs": an expression whose **outermost recognized tag**
carries domain metadata. Catalogue one and its kind is **`expr_builder`**; recover the live domain
object from the entry with **`expr.ls.builder`** and call its methods to build new exprs. This skill is
the **round-tripping and extension layer** — to/from builder entries, and how to make your own objects
round-trip. Fitting models lives in **`ml`**; a fitted pipeline shows up here only as one instance.

**One shape, three instances.** Each produces an expr carrying a builder tag; cataloguing it gives an
`expr_builder` entry that round-trips the same way:

| Builder | Produce the tagged expr | `expr.ls.builder` returns | Re-build by calling |
|---|---|---|---|
| Semantic model (boring-semantic-layer) | `model.query(dimensions=…, measures=…).to_tagged()` | the `SemanticModel` | `.query(…)` |
| Fitted pipeline (**`ml`**) | `fitted.predict(<data>)` / `.transform(<data>)` — already tagged | the `FittedPipeline` | `.predict(…)` / `.transform(…)` |
| Custom | `base.tag("<tag>", **metadata)` | your domain object | whatever it exposes |

**Joins (semantic models).** A model may declare `join_one` / `join_many` / `join_cross` relationships
(authoring is boring-semantic-layer's domain); joined dimensions and measures are **table-prefixed**
(`<table>.<field>`) in both `.query(...)` and the captured metadata. Round-tripping caveat: a recovered
**`join_one`** model re-queries cleanly, but re-querying a recovered **`join_many`** model raises a
dimension-resolution error (bsl 0.3.14) — for a new selection over a one-to-many model, re-run the
stored query or rebuild from source (**B**) instead of re-querying the recovered object.


## A. Recover and re-query (the RECOVER primitive)

A bare `run` executes the entry's stored query; **RECOVER** recovers the builder and re-parameterizes
it — "use an expr builder to make a new expr" — then executes, writing no new entry:

```bash
xorq catalog run <builder-entry> \
  -c 'source.ls.builder.<method>(<params>).to_tagged()' -o - -f json --limit 5
```

- `<method>(<params>)` is the recovered object's **own API** — `.query(dimensions=[…], measures=[…])`
  for a semantic model, `.predict(<data>)` / `.transform(<data>)` for a fitted pipeline, or whatever a
  custom builder exposes.
- `.to_tagged()` re-tags a semantic-model query result so the new expr is itself a builder; a
  fitted-pipeline / custom expr is already tagged, so it's often unnecessary.
- Inspect a builder entry's type and metadata with `xorq catalog show <entry>` (the read-only
  inspection vocabulary is the **`catalog-explore`** skill).

To **persist** a re-parameterized builder as its own `expr_builder` entry, use **B** — `catalog compose`
always records a `composed` entry (see **`composer`**) even when its `-c` ends in `.to_tagged()` (the
compose wrapper is outermost). The builder stays recoverable under that wrapper via `.ls.builder`, but
the entry kind is `composed`, not `expr_builder`.

## B. Build / originate a builder entry (the BUILD-ADD primitive)

When the builder isn't catalogued yet, mint it with **BUILD-ADD**. The only new thing is producing a
**tagged** expression bound to `expr` (the lightest-tool ladder and `expr.ls.*` introspection are in
[reference.md](../_shared/reference.md)):

```python
# build_builder.py — bind the result expression to `expr`
import xorq.api as xo
# construct the builder over a source (a deferred read, or a catalogued expr), then:
expr = <builder-result>
#   semantic model : model.query(dimensions=(…,), measures=(…,)).to_tagged()
#   fitted pipeline: fitted.predict(<data>)            # already builder-tagged
#   custom         : base.tag("<tag>", **metadata)     # needs a registered handler (C)
```

`xorq build build_builder.py --builds-dir builds_builder …` then `catalog add … -a <alias>` →
**kind: `expr_builder`**.

## C. Author a custom TagHandler (the extension layer)

Make any domain object round-trip: declare a **`TagHandler`** mapping your tag to (1) sidecar metadata
and (2) a recovered object, then **`tag`** your expressions with it.

```python
# my_handlers.py
from xorq.expr.builders import TagHandler, register_tag_handler

def _extract_metadata(tag_node):              # -> sidecar dict; always readable via `catalog show`
    m = tag_node.metadata                     # the kwargs you passed to .tag(...), plus "tag"
    return {"type": "<tag>", "description": "<summary>", **{k: m.get(k) for k in (...)}}

def _from_tag_node(tag_node):                 # -> the live object; used by expr.ls.builder
    base = tag_node.parent.to_expr()          # the expression directly below the tag
    return MyBuilder.from_parts(base, tag_node.metadata)

handler = TagHandler(
    tag_names=("<tag>",),
    extract_metadata=_extract_metadata,        # at least one of extract_metadata / from_tag_node
    from_tag_node=_from_tag_node,
    # reemit=...,                              # opt-in: rebuild hook for `catalog replay --rebuild`
)
register_tag_handler(handler)                  # in-process only — see Pitfalls
```

Tag an expression to make it a builder: `base.tag("<tag>", key=value, …)`.

For the handler to be found across **separate processes** (`build` / `catalog add` / `catalog run` each
run in their own, often isolated, env), **register it as an entry point** in the package that ships it:

```toml
# pyproject.toml of the package that defines the handler
[project.entry-points."xorq.from_tag_node"]
my_plugin = "my_package.my_handlers:handler"
```

Built-in handlers (the ML `FittedPipeline`) and entry-point handlers (e.g. boring-semantic-layer's) are
discovered automatically in every process.

## Verify

Run **VERIFY**. For a builder entry, expect:

```bash
xorq catalog list --kind        # expect: <hash>  expr_builder
xorq catalog show <alias>       # "Type: Expression Builder", "Root tag:", a "Builders:" block
xorq catalog run <alias> -c 'source.ls.builder.<method>(<params>).to_tagged()' \
  -o - -f json --limit 5                  # round-trips: recovers the builder and re-queries
```

`expr.ls.expr_traits.has_builders` is a cheap in-process predicate.

## Pitfalls (builder-specific; shared ones are in the kernel)

- **Outermost-tag-only detection.** The kind is the **outermost recognized** builder tag. An
  unrecognized tag is decorative — the entry classifies as its underlying kind (`source` / `expr`).
  Wrapping a builder (e.g. via `catalog compose`) yields a `composed` entry; the builder is still
  recoverable underneath via `.ls.builder`. To mint an `expr_builder`, use **B**.
- **Registration scope is process-local.** `register_tag_handler(...)` lasts only for the current
  process. `build` / `catalog add` / `catalog run` are **separate** processes, so a custom handler must
  be installed as a **`xorq.from_tag_node` entry point** in the build's environment — otherwise
  `.ls.builder` raises `No builder tags found in expression`. The **sidecar** (`extract_metadata`) is
  captured at add time and always reads back; only **live recovery** (`from_tag_node`) needs the handler.
- **`from_tag_node` reconstructs only what the tag captured** — store everything the rebuild needs in
  `.tag(...)` metadata.
- **Tag-key collisions** — a duplicate `tag_names` registration raises (`override=True` to replace);
  built-in tag keys are protected.

## Arguments

If the user provides arguments: $ARGUMENTS — treat them as the builder entry to round-trip (recover +
re-query, or persist a re-parameterized variant), and/or the target catalog (`-p` / `-n`).
