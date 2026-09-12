#!/bin/bash
# Regression test for issue #274.
#
# #274 reported that docs/marc/telemetry-badge.json advertised a fabricated
# "15.2%" savings figure, computed from hardcoded stub telemetry
# (session ids "sess-baseline-*"/"sess-post-*", weighted values
# 15000/8000/12000/7500) that scripts/run_token_benchmark.sh writes on every
# non-release event, combined with the Generate Dashboard Files / Commit
# Dashboard workflow steps running unconditionally. #279 reset the badge to
# "No Data"; this follow-up (still #274, reopened) found docs/marc/telemetry.md
# still rendered the same fabricated numbers via a mermaid chart
# (x-axis [sess-bas, sess-bas], line [15000, 8000]).
#
# This test asserts, offline and deterministically, that neither published
# file contains any of those stub markers, so a future regression (stub data
# reaching a published doc again) fails CI instead of shipping silently.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TELEMETRY_MD="$REPO_ROOT/docs/marc/telemetry.md"
BADGE_JSON="$REPO_ROOT/docs/marc/telemetry-badge.json"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# Stub markers from the non-release branch of run_token_benchmark.sh's main().
STUB_MARKERS=(
    "sess-bas"
    "sess-baseline"
    "sess-post"
    "15000"
    "8000"
    "12000"
    "7500"
    "15.2"
    "stub-model"
)

check_file_clean() {
    local file="$1"
    local label="$2"

    if [ ! -f "$file" ]; then
        fail "$label: file not found at $file"
        return
    fi

    local hit=0
    for marker in "${STUB_MARKERS[@]}"; do
        if grep -qF -- "$marker" "$file"; then
            fail "$label: contains stub marker '$marker'"
            hit=1
        fi
    done

    if [ "$hit" -eq 0 ]; then
        pass "$label: no stub markers found"
    fi
}

check_file_clean "$TELEMETRY_MD" "docs/marc/telemetry.md"
check_file_clean "$BADGE_JSON" "docs/marc/telemetry-badge.json"

# telemetry.md must not render a chart claiming to be real data while none
# has ever been measured.
if [ -f "$TELEMETRY_MD" ] && grep -q 'xychart-beta' "$TELEMETRY_MD"; then
    fail "docs/marc/telemetry.md: still renders a chart (xychart-beta); must be pure text until real telemetry exists"
else
    pass "docs/marc/telemetry.md: no chart rendered"
fi

# The badge must be in the explicit "no data" state (or a future real
# measurement), never blank/missing.
if [ -f "$BADGE_JSON" ] && grep -q '"message"' "$BADGE_JSON"; then
    pass "docs/marc/telemetry-badge.json: has a message field"
else
    fail "docs/marc/telemetry-badge.json: missing 'message' field"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All telemetry no-stub-data checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
