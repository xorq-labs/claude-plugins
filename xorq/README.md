# xorq plugin for Claude

A Claude plugin that exposes the [xorq](https://github.com/xorq-labs/xorq) CLI as MCP tools, letting Claude build, run, and manage versioned data expressions.

## Prerequisites

Install xorq with the `mcp` extra:

```bash
pip install xorq[mcp]
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

### MCP server only (no plugin)

Add to `.claude/settings.json` or `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "xorq": {
      "command": "xorq",
      "args": ["mcp", "serve"]
    }
  }
}
```

## Available tools

### Catalog (read)

| Tool | Description |
|------|-------------|
| `catalog_list` | List all entries in a catalog |
| `catalog_list_aliases` | List all aliases |
| `catalog_schema` | Show input/output schema of an entry |
| `catalog_info` | Show catalog metadata (path, remotes, counts) |
| `catalog_log` | Show catalog history as structured operations |
| `catalog_check` | Validate catalog consistency |

### Build and run

| Tool | Description |
|------|-------------|
| `build` | Compile a Python script into versioned build artifacts |
| `run` | Execute a built expression and return results |
| `run_cached` | Execute with caching for efficient repeated runs |

### Catalog (mutate)

| Tool | Description |
|------|-------------|
| `catalog_add` | Add build artifacts to a catalog |
| `catalog_remove` | Remove entries by name |
| `catalog_sync` | Pull then push to remote(s) |

## Skills

| Skill | Description |
|-------|-------------|
| `/xorq:catalog-explore` | Discover and inspect catalog entries |
| `/xorq:run-expression` | Build and run data expressions |
| `/xorq:recon-import` | Bulk-import CSV and Parquet files into a catalog |

## Architecture

The MCP server wraps CLI commands via subprocess. This keeps the server process lightweight — the heavy `xorq` import only happens in the child process for each tool call.

```
Claude <--stdio--> MCP server <--subprocess--> xorq CLI
```
