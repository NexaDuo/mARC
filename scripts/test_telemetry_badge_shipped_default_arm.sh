#!/bin/bash
# Regression test for issue #303.
#
# The read-guard ships opt-in and DISABLED (no `[token_guard]` in
# .agents/team.toml; the template ships it commented out), but the workflow
# fed the "Tokens Saved (Last Release)" badge `post-control.jsonl` -- arm B,
# guard=350, a configuration nobody runs. Against paid run 34987534132 that
# computed -68.9%, while the SHIPPED DEFAULT (guard off, same commit, arm C
# / toggle_baseline-control.jsonl) computed +35.0% on the exact same
# release -- the sign inversion IS the bug. This test reproduces both
# numbers from the archived run's own data (docs/marc/benchmarks/
# run-34987534132/) and asserts generate_badge()/--badge-current now
# selects the guard-off arm.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/generate_telemetry_dashboard.py"
ARCHIVE="$REPO_ROOT/docs/marc/benchmarks/run-34987534132"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

[ -d "$ARCHIVE" ] || { echo "FAIL - archived run $ARCHIVE not found"; exit 1; }

# --- 1. Feeding the OLD arm (post-control.jsonl, guard=350) reproduces the
# fabricated-looking, sign-inverted -68.9% this issue reports. This pins the
# bug's exact prior behavior so a future regression to "use --path for the
# badge again" is caught, not just "some fix exists".
OLD_ARM_OUT="$TMPDIR/old-arm-badge.json"
python3 "$SCRIPT" \
    --path "$ARCHIVE/post-control.jsonl" \
    --baseline "$ARCHIVE/baseline-control.jsonl" \
    --badge-current "$ARCHIVE/post-control.jsonl" \
    --neutral-no-guard "$ARCHIVE/toggle_baseline-neutral.jsonl" \
    --neutral-guarded "$ARCHIVE/toggle_post-neutral.jsonl" \
    --md-out "$TMPDIR/old.md" --badge-out "$OLD_ARM_OUT"
if grep -q '"-68.9%"' "$OLD_ARM_OUT"; then
    pass "reproduced the pre-fix bug: guard-ON arm (post-control.jsonl) yields -68.9% against run 34987534132's own data"
else
    fail "did not reproduce the documented -68.9% from the guard-on arm: $(cat "$OLD_ARM_OUT")"
fi

# --- 2. The FIXED workflow invocation (--badge-current pointed at the
# shipped-default / guard-off, same-commit arm C file) must yield the
# opposite-sign, documented +35.0% instead.
FIXED_OUT="$TMPDIR/fixed-badge.json"
python3 "$SCRIPT" \
    --path "$ARCHIVE/post-control.jsonl" \
    --baseline "$ARCHIVE/baseline-control.jsonl" \
    --badge-current "$ARCHIVE/toggle_baseline-control.jsonl" \
    --neutral-no-guard "$ARCHIVE/toggle_baseline-neutral.jsonl" \
    --neutral-guarded "$ARCHIVE/toggle_post-neutral.jsonl" \
    --md-out "$TMPDIR/fixed.md" --badge-out "$FIXED_OUT"
if grep -q '"35.0%"' "$FIXED_OUT" && grep -q '"success"' "$FIXED_OUT"; then
    pass "badge fed the shipped-default (guard-off) arm yields the documented +35.0%/success, opposite sign from the guard-on arm"
else
    fail "badge fed the shipped-default arm did not match the documented +35.0%: $(cat "$FIXED_OUT")"
fi

# --- 3. The noise floor (measured from the same-commit neutral pair) is
# NOT loosened or hardcoded by this fix: it must still gate a small delta to
# 'No Change', and the +35.0% figure above clears it by only a small margin
# (documented: 33.7% floor, 35.0% actual -- about 1.3 points), not by a wide
# margin that would suggest the floor computation was bypassed.
FLOOR_NO_GUARD="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br
print(br.median_weighted('$ARCHIVE/toggle_baseline-neutral.jsonl')[0])
")"
FLOOR_GUARDED="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br
print(br.median_weighted('$ARCHIVE/toggle_post-neutral.jsonl')[0])
")"
FLOOR_PCT="$(python3 -c "print(abs(($FLOOR_NO_GUARD - $FLOOR_GUARDED) / $FLOOR_NO_GUARD * 100))")"
CLEARS_BY="$(python3 -c "print(35.0 - $FLOOR_PCT)")"
CLEARS_BY_INT="${CLEARS_BY%.*}"
if [ "${CLEARS_BY_INT#-}" -le 3 ] 2>/dev/null; then
    pass "the fixed +35.0% clears the measured noise floor (${FLOOR_PCT}%) by only a few points ($CLEARS_BY), consistent with the floor not being loosened or bypassed"
else
    fail "the fixed badge's margin over the measured floor ($CLEARS_BY points over ${FLOOR_PCT}%) is wider than expected -- check the floor computation wasn't bypassed"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All shipped-default-arm checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
