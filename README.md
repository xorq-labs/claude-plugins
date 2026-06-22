# xorq plugins for Claude Code

Official Claude Code plugins from xorq-labs. This repo is a plugin marketplace.
Today it ships one plugin, `xorq`, and is structured so more can be added later.

## Install

```
/plugin marketplace add xorq-labs/claude-plugins
/plugin install xorq@xorq-plugins
```

## Plugins

### xorq

Build, version, and run composable data expressions from Claude Code using the xorq CLI.
See [xorq/README.md](./xorq/README.md) for prerequisites, installation, and details.

The plugin adds these skills:

| Skill | What it does |
|-------|--------------|
| `/xorq:ingest` | Create source catalog entries from raw csv, parquet, or database tables. |
| `/xorq:composer` | Build new composed entries from data already in the catalog. |
| `/xorq:builder` | Round-trip ExprBuilder entries and author custom TagHandlers. |
| `/xorq:ml` | Fit, version, and run sklearn pipelines over catalogued data. |
| `/xorq:catalog-explore` | Inspect catalogs read-only: entries, aliases, schemas, history. |
| `/xorq:diagnose` | Read run logs and manage caching when a run fails or serves stale data. |

## Shared context, injected once

The skills share a vocabulary: how to find the xorq CLI, how to resolve which catalog to
operate on, and the core build-add, recover, and verify idioms. Rather than repeat that in
every skill, the plugin keeps it in one file, `xorq/skills/_shared/essentials.md`, and a
SessionStart hook (`xorq/hooks/hooks.json`) prints it into context at the start of each
session. Every skill can then assume it is present.

This works because a plugin's root `CLAUDE.md` is never loaded into context, so the hook is
the supported way to ship ambient plugin context. It fires on install alone, including in
headless `claude -p` runs, which is why the end-to-end tests exercise the real mechanism.
Longer background lives in `xorq/skills/_shared/reference.md` and is read on demand.
