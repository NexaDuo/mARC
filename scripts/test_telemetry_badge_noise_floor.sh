#!/bin/bash
# Regression test for issue #296, updated for issue #308.
#
# Paid run 34987534132 measured the `neutral` task (the task the read-guard
# structurally cannot fire on in either arm), on its SAME-COMMIT pairing
# (arm C / toggle_baseline, no guard, vs arm B / toggle_post, guard=350).
# Publishing a per-release delta smaller than that noise as "savings" would
# automate the exact fabrication issue #274 committed by hand (the "15.2%"
# badge). generate_badge() in scripts/generate_telemetry_dashboard.py must:
#
#   1. Compute the noise floor as a MAD (median absolute deviation) over the
#      pooled, de-duplicated same-commit `neutral` no-guard/guarded
#      observations (never hardcoded, never the inter-release arm A vs B
#      pairing -- that pairing differs by both release AND threshold and
#      would absorb release drift into the floor; caught in PR #297 review;
#      issue #308 replaced the old median-vs-median gap with this MAD
#      estimator because a gap between two medians is a claim about
#      central tendency, not dispersion).
#   2. ALWAYS show the delta as a band (e.g. "-5.0% ±11.1%"), never suppress
#      it below the floor -- issue #308. Color still signals confidence:
#      "informational" when the band [pct-floor, pct+floor] straddles zero
#      (indistinguishable from noise), "success"/"orange" when it doesn't
#      (that state is already covered by
#      test_telemetry_badge_dashboard_same_run.sh's dynamic check).
#   3. Fall back to the honest "No Data" state -- never an ungated number --
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

# Neutral task, same-commit pairing (arm C / toggle_baseline = no guard, vs
# arm B / toggle_post = guard=350 -- re-pointed on PR #297 review from the
# wrong inter-release arm A vs B pairing, which would have absorbed release
# drift into the floor instead of measuring pure noise). Pooled MAD over
# these 10 distinct values (median 9000, MAD 1000) is 11.1%, comfortably
# larger than control's ~5% delta, so control's delta must NOT clear it.
cat > "$TMPDIR/toggle_baseline-neutral.jsonl" <<'JSON'
{"session_id": "n1", "weighted": 7900}
{"session_id": "n2", "weighted": 8000}
{"session_id": "n3", "weighted": 8100}
{"session_id": "n4", "weighted": 7950}
{"session_id": "n5", "weighted": 8050}
JSON
cat > "$TMPDIR/toggle_post-neutral.jsonl" <<'JSON'
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

# --- 1. Within-noise: delta smaller than the measured MAD floor -> the
# number is still SHOWN (issue #308: no more silent suppression), but
# colored "informational" because [pct-floor, pct+floor] straddles zero. --
WITHIN_NOISE_OUT="$TMPDIR/within-noise-badge.json"
if ! run_badge "$WITHIN_NOISE_OUT" \
    --neutral-no-guard "$TMPDIR/toggle_baseline-neutral.jsonl" \
    --neutral-guarded "$TMPDIR/toggle_post-neutral.jsonl"; then
    fail "generate_telemetry_dashboard.py exited non-zero on the within-noise fixture"
elif grep -q '"informational"' "$WITHIN_NOISE_OUT" && grep -qE '±|\\u00b1' "$WITHIN_NOISE_OUT"; then
    pass "badge reports informational/band when the reported delta does not exceed the measured MAD noise floor"
else
    fail "badge did not report an informational band for a delta below the noise floor: $(cat "$WITHIN_NOISE_OUT")"
fi

# The number must still be visible (not discarded) -- issue #308's whole
# point is that suppressing it ("No Change") throws away information.
if grep -q '"message": "-5.0% ' "$WITHIN_NOISE_OUT"; then
    pass "within-noise message still carries the actual delta figure (-5.0%), not a suppressed placeholder"
else
    fail "within-noise badge message did not carry the expected -5.0% figure: $(cat "$WITHIN_NOISE_OUT")"
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
EMPTY_NEUTRAL_NO_GUARD="$TMPDIR/empty-toggle_baseline-neutral.jsonl"
EMPTY_NEUTRAL_GUARDED="$TMPDIR/empty-toggle_post-neutral.jsonl"
: > "$EMPTY_NEUTRAL_NO_GUARD"
: > "$EMPTY_NEUTRAL_GUARDED"
EMPTY_NEUTRAL_OUT="$TMPDIR/empty-neutral-badge.json"
if ! run_badge "$EMPTY_NEUTRAL_OUT" \
    --neutral-no-guard "$EMPTY_NEUTRAL_NO_GUARD" \
    --neutral-guarded "$EMPTY_NEUTRAL_GUARDED"; then
    fail "generate_telemetry_dashboard.py exited non-zero with empty --neutral-* files"
elif grep -q '"No Data"' "$EMPTY_NEUTRAL_OUT" && grep -q '"inactive"' "$EMPTY_NEUTRAL_OUT"; then
    pass "badge falls back to honest 'No Data'/inactive when neutral files exist but yield no floor"
else
    fail "badge published something other than 'No Data' with empty neutral files: $(cat "$EMPTY_NEUTRAL_OUT")"
fi

# --- 4. Double-counting regression guard (issue #308's core trap): a
# fixture where the same-commit "guarded" neutral file is BYTE-IDENTICAL to
# a hypothetical extra "post" file (mirroring run_token_benchmark.sh's real
# `cp post-<task>.jsonl toggle_post-<task>.jsonl`) must not be pooled twice.
# Pool the SAME file twice via --neutral-guarded pointed at a duplicate path
# of --neutral-no-guard's pair partner and assert the floor computed from 2
# distinct files (10 values) is unaffected by ALSO handing mad_floor a
# duplicate of one of them directly, at the Python level.
DEDUP_CHECK="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br

a = '$TMPDIR/toggle_baseline-neutral.jsonl'
b = '$TMPDIR/toggle_post-neutral.jsonl'

# Correct: pool the 2 distinct arms -> 10 observations.
correct = br.mad_floor([a, b])

# Buggy shape this test exists to catch: naively pooling 'post-neutral.jsonl'
# (byte-identical copy of b, mirroring run_token_benchmark.sh's cp) AS WELL
# AS b itself must NOT double b's 5 values to 10 (20 total) -- de-duplication
# must collapse the identical-content file back down to the same 10.
import shutil
dup = '$TMPDIR/post-neutral.jsonl'
shutil.copyfile(b, dup)
deduped = br.mad_floor([a, b, dup])

print(correct.n, deduped.n)
")"
CORRECT_N="$(echo "$DEDUP_CHECK" | awk '{print $1}')"
DEDUPED_N="$(echo "$DEDUP_CHECK" | awk '{print $2}')"
if [ "$CORRECT_N" = "10" ] && [ "$DEDUPED_N" = "10" ]; then
    pass "mad_floor() pools by distinct arm: handing it a byte-identical extra file still yields n=10, not 15/20 (issue #308 double-counting trap)"
else
    fail "mad_floor() double-counted a byte-identical duplicate file: got n=$CORRECT_N (2 distinct files) and n=$DEDUPED_N (with a duplicate added), expected 10 and 10"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All telemetry badge noise-floor checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
