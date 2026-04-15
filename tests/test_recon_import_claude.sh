#!/usr/bin/env bash
# Claude acceptance test for the recon-import skill.
# Verifies that Claude follows the skill instructions and produces correct catalog entries.
# Requires: claude CLI on PATH
set -euo pipefail

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

WORKDIR="$TMPDIR/project"
DATADIR="$WORKDIR/data"
CATALOG="$WORKDIR/catalog"

mkdir -p "$DATADIR"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# --- Setup: sample data ---

cat > "$DATADIR/sales.csv" << 'CSV'
date,product,quantity,price
2026-01-01,Widget,10,29.99
2026-01-02,Gadget,5,49.99
2026-01-03,Widget,8,29.99
CSV

cat > "$DATADIR/regions.csv" << 'CSV'
region,manager
North,Alice
South,Bob
East,Carol
CSV

# --- Setup: git init (Claude needs project detection) ---

cd "$WORKDIR"
git init -q
git add -A
git commit -q -m "init"

# --- Setup: CLAUDE.md ---

cat > "$WORKDIR/CLAUDE.md" << EOF
Data files are in the \`data/\` directory.
The catalog is at \`catalog/\`.
Use absolute paths when writing import scripts.
EOF

# --- Setup: permissions ---

mkdir -p "$WORKDIR/.claude"
cat > "$WORKDIR/.claude/settings.local.json" << 'JSON'
{
  "permissions": {
    "allow": [
      "Bash(uvx xorq*)",
      "Bash(rm *)",
      "Write(*)",
      "Read(*)",
      "Glob(*)"
    ]
  }
}
JSON

# --- Init catalog ---

echo "=== Init catalog ==="
uvx xorq catalog --path "$CATALOG" init 2>/dev/null
git -C "$WORKDIR" add -A && git -C "$WORKDIR" commit -q -m "add data and catalog"

# --- Run Claude ---

echo "=== Running Claude ==="

PLUGIN_DIR="/Users/paddy/-plugins/xorq"

PROMPT="Import all CSV files from the data/ directory into the catalog at catalog/. Use the /xorq:recon-import skill."

claude -p "$PROMPT" \
  --output-format text \
  --max-turns 15 \
  --permission-mode bypassPermissions \
  --plugin-dir "$PLUGIN_DIR" \
  -d "$WORKDIR" \
  2>/dev/null || true

# --- Verify results (don't trust Claude's output) ---

echo "=== Verification ==="

# Check aliases exist
ALIASES=$(uvx xorq catalog --path "$CATALOG" list-aliases 2>/dev/null)
echo "Aliases: $ALIASES"

if echo "$ALIASES" | grep -q "sales"; then pass "sales alias exists"; else fail "sales alias not found"; fi
if echo "$ALIASES" | grep -q "regions"; then pass "regions alias exists"; else fail "regions alias not found"; fi

# Check schemas
SALES_SCHEMA=$(uvx xorq catalog --path "$CATALOG" schema sales 2>/dev/null)
if echo "$SALES_SCHEMA" | grep -q "product"; then pass "sales has product column"; else fail "sales missing product column"; fi
if echo "$SALES_SCHEMA" | grep -q "price"; then pass "sales has price column"; else fail "sales missing price column"; fi

REGIONS_SCHEMA=$(uvx xorq catalog --path "$CATALOG" schema regions 2>/dev/null)
if echo "$REGIONS_SCHEMA" | grep -q "region"; then pass "regions has region column"; else fail "regions missing region column"; fi
if echo "$REGIONS_SCHEMA" | grep -q "manager"; then pass "regions has manager column"; else fail "regions missing manager column"; fi

# Check portability (run the entries)
SALES_RUN=$(uvx xorq catalog --path "$CATALOG" run sales --limit 3 -f csv -o /dev/stdout 2>/dev/null)
if echo "$SALES_RUN" | grep -q "Widget"; then pass "sales runs correctly"; else fail "sales run failed: $SALES_RUN"; fi

REGIONS_RUN=$(uvx xorq catalog --path "$CATALOG" run regions --limit 3 -f csv -o /dev/stdout 2>/dev/null)
if echo "$REGIONS_RUN" | grep -q "Alice"; then pass "regions runs correctly"; else fail "regions run failed: $REGIONS_RUN"; fi

# --- Summary ---

echo ""
echo "=== Results ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"

if [ "$FAIL" -gt 0 ]; then exit 1; fi
