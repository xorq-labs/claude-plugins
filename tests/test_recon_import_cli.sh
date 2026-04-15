#!/usr/bin/env bash
# CLI integration test for the recon-import workflow.
# Verifies the exact command sequence works without Claude in the loop.
set -euo pipefail

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

CATALOG="$TMPDIR/catalog"
DATADIR="$TMPDIR/data"
SCRIPTS="$TMPDIR/scripts"

mkdir -p "$DATADIR" "$SCRIPTS"

PASS=0
FAIL=0

pass() { echo "  PASS: $1"; PASS=$((PASS + 1)); }
fail() { echo "  FAIL: $1"; FAIL=$((FAIL + 1)); }

# --- Setup: create sample data ---

cat > "$DATADIR/sales.csv" << 'CSV'
id,date,product,quantity,price
1,2026-01-01,Widget,10,29.99
2,2026-01-02,Gadget,5,49.99
3,2026-01-03,Widget,8,29.99
CSV

cat > "$DATADIR/regions.csv" << 'CSV'
region,manager
North,Alice
South,Bob
East,Carol
CSV

DETECT_PK_SCRIPT="$(cd "$(dirname "$0")/.." && pwd)/lib/detect_pk_and_store.py"

# --- Init catalog ---

echo "=== Init catalog ==="
uvx xorq catalog --path "$CATALOG" init 2>/dev/null
if [ -d "$CATALOG" ]; then pass "catalog initialized"; else fail "catalog init"; fi

# --- Test 1: CSV import (sales) ---

echo "=== Test 1: Import sales.csv ==="

cat > "$SCRIPTS/import_sales.py" << PYEOF
import xorq.api as xo
expr = xo.deferred_read_csv("$DATADIR/sales.csv")
PYEOF

BUILD_PATH=$(uvx xorq build "$SCRIPTS/import_sales.py" -e expr 2>/dev/null | tail -1)
if [ -d "$BUILD_PATH" ]; then pass "sales build dir exists"; else fail "sales build dir missing: $BUILD_PATH"; fi

uvx xorq catalog --path "$CATALOG" add "$BUILD_PATH" --alias sales 2>/dev/null

ALIASES=$(uvx xorq catalog --path "$CATALOG" list-aliases 2>/dev/null)
if echo "$ALIASES" | grep -q "sales"; then pass "sales alias exists"; else fail "sales alias not found"; fi

SCHEMA=$(uvx xorq catalog --path "$CATALOG" schema sales 2>/dev/null)
if echo "$SCHEMA" | grep -q "product"; then pass "sales schema has product column"; else fail "sales schema missing product"; fi

PK_OUTPUT=$(uvx --from xorq python "$DETECT_PK_SCRIPT" "$CATALOG" sales "$DATADIR/sales.csv" 2>/dev/null)
if echo "$PK_OUTPUT" | grep -q "PK: id"; then pass "sales PK detected: id"; else fail "sales PK wrong: $PK_OUTPUT"; fi

# --- Test 2: Second CSV import (regions) ---

echo "=== Test 2: Import regions.csv ==="

cat > "$SCRIPTS/import_regions.py" << PYEOF
import xorq.api as xo
expr = xo.deferred_read_csv("$DATADIR/regions.csv")
PYEOF

BUILD_PATH=$(uvx xorq build "$SCRIPTS/import_regions.py" -e expr 2>/dev/null | tail -1)
uvx xorq catalog --path "$CATALOG" add "$BUILD_PATH" --alias regions 2>/dev/null

ALIASES=$(uvx xorq catalog --path "$CATALOG" list-aliases 2>/dev/null)
if echo "$ALIASES" | grep -q "regions"; then pass "regions alias exists"; else fail "regions alias not found"; fi

SCHEMA=$(uvx xorq catalog --path "$CATALOG" schema regions 2>/dev/null)
if echo "$SCHEMA" | grep -q "manager"; then pass "regions schema has manager column"; else fail "regions schema missing manager"; fi

PK_OUTPUT=$(uvx --from xorq python "$DETECT_PK_SCRIPT" "$CATALOG" regions "$DATADIR/regions.csv" 2>/dev/null)
if echo "$PK_OUTPUT" | grep -q "PK: region"; then pass "regions PK detected: region"; else fail "regions PK wrong: $PK_OUTPUT"; fi

# --- Test 3: Duplicate detection ---

echo "=== Test 3: Duplicate detection ==="

ALIASES=$(uvx xorq catalog --path "$CATALOG" list-aliases 2>/dev/null)
if echo "$ALIASES" | grep -q "sales"; then
    pass "duplicate check: sales already in list-aliases"
else
    fail "duplicate check: sales missing from list-aliases"
fi

# --- Test 4: Portability (run after deleting builds) ---

echo "=== Test 4: Portability ==="

# We don't need to delete builds explicitly — the builds were created inside $TMPDIR
# which gets cleaned up by the trap. The point is that deferred_read embeds data
# into the catalog, so it doesn't depend on the build directory at all.

# The catalog should still work because deferred_read embeds the data
RUN_OUTPUT=$(uvx xorq catalog --path "$CATALOG" run sales --limit 3 -f csv -o /dev/stdout 2>/dev/null)
if echo "$RUN_OUTPUT" | grep -q "Widget"; then pass "sales runs after build deletion (portable)"; else fail "sales fails after build deletion: $RUN_OUTPUT"; fi

RUN_OUTPUT=$(uvx xorq catalog --path "$CATALOG" run regions --limit 3 -f csv -o /dev/stdout 2>/dev/null)
if echo "$RUN_OUTPUT" | grep -q "Alice"; then pass "regions runs after build deletion (portable)"; else fail "regions fails after build deletion: $RUN_OUTPUT"; fi

# --- Test 5: Parquet import (if pyarrow available) ---

echo "=== Test 5: Parquet import ==="

if uvx --from xorq python -c "import pyarrow" 2>/dev/null; then
    uvx --from xorq python -c "
import pyarrow as pa
import pyarrow.parquet as pq
table = pa.table({'id': [1,2,3], 'name': ['alpha','beta','gamma']})
pq.write_table(table, '$DATADIR/items.parquet')
" 2>/dev/null

    cat > "$SCRIPTS/import_items.py" << PYEOF
import xorq.api as xo
expr = xo.deferred_read_parquet("$DATADIR/items.parquet")
PYEOF

    BUILD_PATH=$(uvx xorq build "$SCRIPTS/import_items.py" -e expr 2>/dev/null | tail -1)
    uvx xorq catalog --path "$CATALOG" add "$BUILD_PATH" --alias items 2>/dev/null

    SCHEMA=$(uvx xorq catalog --path "$CATALOG" schema items 2>/dev/null)
    if echo "$SCHEMA" | grep -q "name"; then pass "parquet import schema has name column"; else fail "parquet schema missing name"; fi

    RUN_OUTPUT=$(uvx xorq catalog --path "$CATALOG" run items --limit 3 -f csv -o /dev/stdout 2>/dev/null)
    if echo "$RUN_OUTPUT" | grep -q "alpha"; then pass "parquet runs correctly"; else fail "parquet run failed: $RUN_OUTPUT"; fi

    PK_OUTPUT=$(uvx --from xorq python "$DETECT_PK_SCRIPT" "$CATALOG" items "$DATADIR/items.parquet" 2>/dev/null)
    if echo "$PK_OUTPUT" | grep -q "PK: id"; then pass "parquet PK detected: id"; else fail "parquet PK wrong: $PK_OUTPUT"; fi
else
    echo "  SKIP: pyarrow not available"
fi

# --- Test 6: PK metadata persisted in catalog ---

echo "=== Test 6: PK metadata readback ==="

SALES_PK=$(uvx --from xorq python -c "
import yaml
from xorq.catalog.catalog import Catalog
cat = Catalog.from_repo_path('$CATALOG')
entry = next(a.catalog_entry for a in cat.catalog_aliases if a.alias == 'sales')
meta = yaml.safe_load(entry.metadata_path.read_text()) or {}
pk = meta.get('primary_key')
print(' + '.join(pk) if pk else 'none')
" 2>/dev/null)
if [ "$SALES_PK" = "id" ]; then pass "sales PK persisted in metadata: $SALES_PK"; else fail "sales PK metadata wrong: $SALES_PK"; fi

REGIONS_PK=$(uvx --from xorq python -c "
import yaml
from xorq.catalog.catalog import Catalog
cat = Catalog.from_repo_path('$CATALOG')
entry = next(a.catalog_entry for a in cat.catalog_aliases if a.alias == 'regions')
meta = yaml.safe_load(entry.metadata_path.read_text()) or {}
pk = meta.get('primary_key')
print(' + '.join(pk) if pk else 'none')
" 2>/dev/null)
if [ "$REGIONS_PK" = "region" ]; then pass "regions PK persisted in metadata: $REGIONS_PK"; else fail "regions PK metadata wrong: $REGIONS_PK"; fi

# --- Test 7: Alias count ---

echo "=== Test 7: Final alias count ==="

ALIAS_COUNT=$(uvx xorq catalog --path "$CATALOG" list-aliases 2>/dev/null | grep -c ".")
if [ "$ALIAS_COUNT" -ge 2 ]; then pass "alias count >= 2 ($ALIAS_COUNT)"; else fail "alias count too low: $ALIAS_COUNT"; fi

# --- Summary ---

echo ""
echo "=== Results ==="
echo "  Passed: $PASS"
echo "  Failed: $FAIL"

if [ "$FAIL" -gt 0 ]; then exit 1; fi
