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
| `/xorq:run-expression` | Build and run data expressions |

## Hooks

The plugin includes hooks that enforce xorq best practices and suggest skills contextually.

| Hook | Event | Purpose |
|------|-------|---------|
| `skill_activation.py` | UserPromptSubmit | Match prompt against skill rules, suggest relevant skills |
| `guardrail_check.py` | PreToolUse (Edit/Write) | Block xorq anti-patterns (memtable, cases, eager execution) |
| `post_tool_use.py` | PostToolUse (Edit/Write) | Detect xorq patterns in edits, suggest skills |
| `post_tool_use_failure.py` | PostToolUse (Edit/Write/Bash) | xorq-specific error troubleshooting |
| `stop_check.py` | Stop | One-time reminder to catalog expressions edited this session |

### Guardrails

Three guardrails block common xorq anti-patterns in Edit/Write:

- **no-memtable** — blocks `ibis.memtable()` which embeds local paths and breaks portability
- **no-cases-pattern** — blocks `xo.cases()` / `ibis.cases()` which don't survive serialization; use `xo.case()` builder instead
- **no-eager-execution** — blocks `.execute()` / `.to_pandas()` in build scripts; everything must stay deferred

Add `# @skip-validation` to a file or set `SKIP_XORQ_GUARDRAILS=1` to bypass.

### Data tool enforcement

Bash commands are checked for non-xorq data tool usage (pandas, polars, raw duckdb/postgres/snowflake, standalone ibis). When detected, the hook nudges toward xorq equivalents (`deferred_read_csv`, `xo.connect()`, etc.) and suggests the appropriate skill. This is a suggestion, not a hard block.

### Configuration

Hook registrations are in `hooks/hooks.json`. Skill trigger rules and guardrail definitions are in `hooks/skill-rules.json`.

## Architecture

The plugin provides skills that guide Claude to use the `xorq` CLI via Bash. No MCP server is required — Claude invokes CLI commands directly.

```
Claude --skills--> xorq CLI --subprocess--> xorq engine
       --hooks---> guardrails + skill suggestions
```

### Ambient context

`CLAUDE.md` provides Claude with background knowledge about xorq concepts (expressions, catalogs, ExprKind, TagHandlers, ML pipelines) so it can assist effectively even outside of explicit skill invocations.
