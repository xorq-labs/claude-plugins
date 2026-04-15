# Plan: recon-import Skill

## Context

The xorq plugin at `/Users/paddy/-plugins/xorq/` needs a `recon-import` skill that bulk-imports CSV and Parquet files into a xorq catalog. Unlike the two existing skills (which use MCP tools), this one uses `uvx xorq` CLI commands via Bash. The cloud MCP server has `xorq_recon_import` as a tool, but the local plugin doesn't have access to that — so we replicate the workflow with CLI commands.

## Deliverables

1. `xorq/skills/recon-import/SKILL.md` — the skill prompt
2. `tests/test_recon_import_cli.sh` — CLI integration test (fast, no Claude)
3. `tests/test_recon_import_claude.sh` — Claude acceptance test (slow, verifies skill)

## 1. Skill File

**Path:** `xorq/skills/recon-import/SKILL.md`

The skill instructs Claude to:
1. Find files matching a glob pattern (via Glob or Bash `ls`)
2. Check existing aliases with `uvx xorq catalog list-aliases` to skip duplicates
3. For each new file:
   - Write a temp Python script using `xo.deferred_read_csv("<abs_path>")` or `xo.deferred_read_parquet("<abs_path>")` — these produce portable catalog entries (see `docs/bugs/catalog-absolute-paths.md`)
   - Build: `uvx xorq build <script> -e expr` — capture last line of stdout as build path
   - Add: `uvx xorq catalog add <build_path> --alias <stem>`
4. Verify each import: `uvx xorq catalog schema <alias>`
5. Summarize: imported / skipped / failed

Key design decisions:
- **Explicit "use CLI, not MCP"** — other skills use MCP tools; without this, Claude defaults to MCP
- **`deferred_read_csv/parquet` not `read_csv/parquet`** — `xo.read_csv()` produces non-portable entries with absolute paths; `xo.deferred_read_csv()` embeds the data so the catalog works after build deletion and on other machines
- **Absolute paths in temp scripts** — `xorq build` may resolve paths differently
- **Alias = filename stem** — matches the cloud `xorq_recon_import` behavior
- **`--path` flag required** — skill must tell Claude where the catalog lives

## 2. CLI Integration Test (no Claude in the loop)

**Path:** `tests/test_recon_import_cli.sh`

Purpose: Verify the exact CLI command sequence works. This is the fast feedback loop (~10s, no LLM calls).

**Setup:**
- Create temp dir with sample CSV (sales: date/product/quantity/price), second CSV (regions: region/manager), and optionally a Parquet file (via pyarrow if available)
- Init a catalog: `uvx xorq catalog --path $CATALOG init`

**Test cases:**
1. **CSV import** — write script, build, add, verify schema columns
2. **Second CSV import** — verify multiple imports produce multiple aliases
3. **Duplicate detection** — check `list-aliases` catches existing stems before re-importing
4. **Parquet import** — same workflow with `.parquet` file (skip if pyarrow unavailable)
5. **Final alias count** — verify expected total

**Assertions per step:**
- `build` produces a directory that exists
- `catalog add --alias X` makes X appear in `list-aliases`
- `catalog schema X` contains expected column names
- Alias count matches expected total

**Cleanup:** `trap 'rm -rf "$TMPDIR"' EXIT`

## 3. Claude Acceptance Test

**Path:** `tests/test_recon_import_claude.sh`

Purpose: Verify Claude follows the skill instructions and the catalog ends up correct. Uses `claude -p` subprocess pattern from `claude_harness.py`.

**Setup:**
- Create temp work dir with `git init` (Claude needs project detection)
- Write sample CSVs to `data/`
- Init catalog at `catalog/`
- Write `CLAUDE.md` pointing to the data dir and catalog path
- Write `.claude/settings.local.json` with permissions for `Bash(uvx xorq*)`, `Write(*)`, `Read(*)`

**Invocation:**
```bash
claude -p "<prompt>" \
  --output-format text \
  --max-turns 10 \
  --permission-mode bypassPermissions \
  --plugin-dir /Users/paddy/-plugins/xorq
```

The prompt asks Claude to import all CSVs from `data/` into the catalog at `catalog/`.

**Verification (post-hoc, not trusting Claude's output):**
- `uvx xorq catalog --path catalog list-aliases` contains `sales` and `regions`
- `uvx xorq catalog --path catalog schema sales` contains date, product, quantity, price
- `uvx xorq catalog --path catalog schema regions` contains region, manager

**This is a shell script**, not Python — keeps the plugin repo dependency-free and matches the manual-qa pattern from xorq-cloud-planning.

## Sequencing

1. Write the skill file
2. Write + run the CLI test — iterate on skill wording if any commands fail
3. Write + run the Claude test — iterate on skill wording if Claude doesn't follow it
4. Update `xorq/README.md` to list the new skill

## Edge Cases to Watch

- **Build path extraction**: `xorq build` may print warnings before the path — use `tail -1` on stdout
- **Alias normalization**: stems with hyphens (`my-data.csv`) need underscore conversion — skill should mention this
- **Missing catalog**: skill must handle "catalog doesn't exist" by either initing or asking
- **`uvx` cold start**: first `uvx xorq` call downloads packages (~5s) — tests should tolerate this

## Files

| File | Action |
|------|--------|
| `xorq/skills/recon-import/SKILL.md` | Create |
| `tests/test_recon_import_cli.sh` | Create |
| `tests/test_recon_import_claude.sh` | Create |
| `xorq/README.md` | Update (add skill to table) |

## Reference files
- `xorq/skills/catalog-explore/SKILL.md` — format reference
- `xorq/skills/run-expression/SKILL.md` — format reference
- `xorq-cloud-planning/packages/worker/src/xorq_cloud_worker/executor.py:597-692` — cloud implementation
- `xorq-cloud-planning/tests/benchmark/claude_harness.py` — `claude -p` pattern
- `xorq-cloud-planning/scripts/manual-qa/test-mcp-all.sh` — shell test pattern
