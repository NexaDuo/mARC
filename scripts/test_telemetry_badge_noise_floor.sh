#!/bin/bash
# Regression test for issue #296.
#
# Paid run 34987534132 measured the `neutral` task (the task the read-guard
# structurally cannot fire on in either arm) at a 33.7% baseline-vs-post gap
# -- pure measurement noise, since nothing under test could have moved it.
# Publishing a per-release delta smaller than that gap as "savings" would
# automate the exact fabrication issue #274 committed by hand (the "15.2%"
# badge). generate_badge() in scripts/generate_telemetry_dashboard.py must:
#
#   1. Compute the noise floor from THIS run's own `neutral` baseline/post
#      pair (never hardcoded).
#   2. Report "No Change" (not a number) when the reported task's delta does
#      not exceed that floor.
#   3. Still report a real percentage when the delta DOES exceed the floor
#      (this state is already covered by
#      test_telemetry_badge_dashboard_same_run.sh's dynamic check).
#   4. Fall back to the honest "No Data" state -- never an ungated number --
#      when `neutral` data is missing, even if baseline/current data for the
#      reported task is perfectly valid.
#
# Offline and deterministic: synthetic JSONL fixtures only, no `claude`
# invocations, no network.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/generate_telemetry_dashboard.py"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

# Control task: ~5% delta (10000 -> 10500 median), a real but small move.
cat > "$TMPDIR/baseline-control.jsonl" <<'JSON'
{"session_id": "c1", "weighted": 9900}
{"session_id": "c2", "weighted": 10000}
{"session_id": "c3", "weighted": 10100}
{"session_id": "c4", "weighted": 9950}
{"session_id": "c5", "weighted": 10050}
JSON
cat > "$TMPDIR/post-control.jsonl" <<'JSON'
{"session_id": "c6", "weighted": 10400}
{"session_id": "c7", "weighted": 10500}
{"session_id": "c8", "weighted": 10600}
{"session_id": "c9", "weighted": 10450}
{"session_id": "c10", "weighted": 10550}
JSON

# Neutral task: ~25% delta -- larger than control's ~5%, so it IS the noise
# floor and control's delta must NOT clear it.
cat > "$TMPDIR/baseline-neutral.jsonl" <<'JSON'
{"session_id": "n1", "weighted": 7900}
{"session_id": "n2", "weighted": 8000}
{"session_id": "n3", "weighted": 8100}
{"session_id": "n4", "weighted": 7950}
{"session_id": "n5", "weighted": 8050}
JSON
cat > "$TMPDIR/post-neutral.jsonl" <<'JSON'
{"session_id": "n6", "weighted": 9900}
{"session_id": "n7", "weighted": 10000}
{"session_id": "n8", "weighted": 10100}
{"session_id": "n9", "weighted": 9950}
{"session_id": "n10", "weighted": 10050}
JSON

run_badge() {
    local badge_out="$1"
    shift
    python3 "$SCRIPT" \
        --path "$TMPDIR/post-control.jsonl" \
        --baseline "$TMPDIR/baseline-control.jsonl" \
        --md-out "$TMPDIR/telemetry.md" \
        --badge-out "$badge_out" \
        "$@"
}

# --- 1. Within-noise: delta below the measured floor -> "No Change" -------
WITHIN_NOISE_OUT="$TMPDIR/within-noise-badge.json"
if ! run_badge "$WITHIN_NOISE_OUT" \
    --neutral-path "$TMPDIR/post-neutral.jsonl" \
    --neutral-baseline "$TMPDIR/baseline-neutral.jsonl"; then
    fail "generate_telemetry_dashboard.py exited non-zero on the within-noise fixture"
elif grep -q '"No Change"' "$WITHIN_NOISE_OUT" && grep -q '"informational"' "$WITHIN_NOISE_OUT"; then
    pass "badge reports 'No Change'/informational when the reported delta does not exceed the measured noise floor"
else
    fail "badge did not report 'No Change' for a delta below the noise floor: $(cat "$WITHIN_NOISE_OUT")"
fi

# It must not read as a savings figure: no bare percentage in the message.
if grep -q '"message": "No Change"' "$WITHIN_NOISE_OUT"; then
    pass "within-noise message carries no percentage figure"
else
    fail "within-noise badge message is not the expected 'No Change' literal: $(cat "$WITHIN_NOISE_OUT")"
fi

# --- 2. Missing neutral data -> honest "No Data", never an ungated number -
# Same control baseline/current as above (a perfectly valid comparison on
# its own) but no --neutral-* flags at all. The badge must NOT publish
# control's raw delta just because neutral is unavailable.
NO_NEUTRAL_OUT="$TMPDIR/no-neutral-badge.json"
if ! run_badge "$NO_NEUTRAL_OUT"; then
    fail "generate_telemetry_dashboard.py exited non-zero without --neutral-*"
elif grep -q '"No Data"' "$NO_NEUTRAL_OUT" && grep -q '"inactive"' "$NO_NEUTRAL_OUT"; then
    pass "badge falls back to honest 'No Data'/inactive when neutral data (the noise floor) is missing, even though control baseline/current data is valid"
else
    fail "badge published something other than 'No Data' with no noise floor available -- an ungated number: $(cat "$NO_NEUTRAL_OUT")"
fi

# --- 3. Neutral files present but empty -> also "No Data" (no usable floor)
EMPTY_NEUTRAL_BASE="$TMPDIR/empty-baseline-neutral.jsonl"
EMPTY_NEUTRAL_POST="$TMPDIR/empty-post-neutral.jsonl"
: > "$EMPTY_NEUTRAL_BASE"
: > "$EMPTY_NEUTRAL_POST"
EMPTY_NEUTRAL_OUT="$TMPDIR/empty-neutral-badge.json"
if ! run_badge "$EMPTY_NEUTRAL_OUT" \
    --neutral-path "$EMPTY_NEUTRAL_POST" \
    --neutral-baseline "$EMPTY_NEUTRAL_BASE"; then
    fail "generate_telemetry_dashboard.py exited non-zero with empty --neutral-* files"
elif grep -q '"No Data"' "$EMPTY_NEUTRAL_OUT" && grep -q '"inactive"' "$EMPTY_NEUTRAL_OUT"; then
    pass "badge falls back to honest 'No Data'/inactive when neutral files exist but yield no floor"
else
    fail "badge published something other than 'No Data' with empty neutral files: $(cat "$EMPTY_NEUTRAL_OUT")"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All telemetry badge noise-floor checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
