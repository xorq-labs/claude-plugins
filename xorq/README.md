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
| `/xorq:init` | Ingest CSV/Parquet files into a catalog |
| `/xorq:composer` | Compose catalog entries into new aliased expressions |
| `/xorq:builder` | Create ExprBuilder entries (ML pipelines, BSL, custom TagHandlers) |
| `/xorq:catalog-explore` | Discover and inspect catalog entries |

## Architecture

The plugin provides skills that guide Claude to use the `xorq` CLI via Bash. No MCP server is required — Claude invokes CLI commands directly.

```
Claude --skills--> xorq CLI --subprocess--> xorq engine
```

### Ambient context

`CLAUDE.md` provides Claude with background knowledge about xorq concepts (expressions, catalogs, ExprKind, TagHandlers, ML pipelines) so it can assist effectively even outside of explicit skill invocations.
