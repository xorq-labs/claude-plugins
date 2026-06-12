# xorq plugin for Claude

A Claude plugin for [xorq](https://github.com/xorq-labs/xorq) — build, version, and run composable data expressions from Claude Code using the xorq CLI.

## Prerequisites

Install xorq:

```bash
pip install xorq
```

The `xorq` command must be on your `PATH`.

## Installation

### From the marketplace

```
/plugin marketplace add xorq-labs/claude-plugins
/plugin install xorq@xorq-plugins
```

### Local development

```bash
claude --plugin-dir ./xorq
```

## Skills

| Skill | Description |
|-------|-------------|
| `/xorq:ingest` | Originate `source` entries from raw data (csv / parquet / db) by building |
| `/xorq:composer` | Compose catalogued expressions into new `composed` entries |
| `/xorq:builder` | Work with `ExprBuilder` entries and custom `TagHandler`s |
| `/xorq:ml` | Fit, version, and run ML models on catalogued data (`FittedPipeline`) |
| `/xorq:catalog-explore` | Find and inspect catalogs read-only — entries, aliases, schemas, history |

## Architecture

The plugin provides skills that guide Claude to use the `xorq` CLI via Bash. No MCP server is required — Claude invokes CLI commands directly.

```
Claude --skills--> xorq CLI --subprocess--> xorq engine
```

### Shared essentials

The plugin ships **no `CLAUDE.md`** — a plugin's root `CLAUDE.md` is never loaded into Claude's context. Instead, a **`SessionStart` hook** (`hooks/hooks.json`) prints `skills/_shared/essentials.md` into context once per session: one source of truth for the shared vocabulary (environment, catalog resolution, and the build→add / recover / verify idioms), present before any skill runs. It's the supported way to ship ambient plugin context — it fires on the plugin install alone, including in headless `claude -p`, so the e2e tests exercise the real mechanism. Deeper background lives in `skills/_shared/reference.md`, linked from the skills and read on demand.
