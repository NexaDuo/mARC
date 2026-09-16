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
if grep -q '"-68.9% ' "$OLD_ARM_OUT"; then
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
if grep -q '"35.0% ' "$FIXED_OUT" && grep -q '"success"' "$FIXED_OUT"; then
    pass "badge fed the shipped-default (guard-off) arm yields the documented +35.0%/success, opposite sign from the guard-on arm"
else
    fail "badge fed the shipped-default arm did not match the documented +35.0%: $(cat "$FIXED_OUT")"
fi

# --- 3. The noise floor (issue #308: MAD over the pooled, de-duplicated
# same-commit neutral pair, not the old median-vs-median gap) is not
# loosened or hardcoded by this fix: the +35.0% figure above must still
# only barely clear it, not by a wide margin that would suggest the floor
# computation was bypassed. The message itself must also carry the band
# (± floor), not a bare percentage -- issue #308 stopped suppressing/hiding
# the uncertainty rather than the delta.
FLOOR_PCT="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br
floor = br.mad_floor(['$ARCHIVE/toggle_baseline-neutral.jsonl', '$ARCHIVE/toggle_post-neutral.jsonl'])
print(floor.mad_pct)
")"
CLEARS_BY="$(python3 -c "print(35.0 - $FLOOR_PCT)")"
CLEARS_BY_INT="${CLEARS_BY%.*}"
if [ "${CLEARS_BY_INT#-}" -le 5 ] 2>/dev/null; then
    pass "the fixed +35.0% clears the measured MAD noise floor (${FLOOR_PCT}%) by only a few points ($CLEARS_BY), consistent with the floor not being loosened or bypassed"
else
    fail "the fixed badge's margin over the measured floor ($CLEARS_BY points over ${FLOOR_PCT}%) is wider than expected -- check the floor computation wasn't bypassed"
fi
if grep -qE '±|\\u00b1' "$FIXED_OUT"; then
    pass "the fixed badge's message carries the noise-floor band (±), not a bare unqualified percentage"
else
    fail "the fixed badge's message did not carry a band: $(cat "$FIXED_OUT")"
fi

# --- 4. STATIC pin on the workflow wiring itself (`@rev` review on PR #305,
# MEDIUM). Parts 1-3 above only prove generate_telemetry_dashboard.py's
# --badge-current flag behaves correctly when given the right value -- they
# say nothing about .github/workflows/token-benchmark.yml actually PASSING
# that value. That gap is exactly the bug class issue #303 was (the badge
# silently pointed at the wrong arm; every other test still passed). This
# extracts the "Generate Dashboard Files" step's run: script from the real
# workflow file and asserts --badge-current is present AND pinned to the
# shipped-default guard-off arm, failing on BOTH regression shapes: the flag
# renamed/re-pointed at the wrong file, or the flag dropped entirely
# (falling back to --path's guard-on arm B, the original #303 bug).
WORKFLOW="$REPO_ROOT/.github/workflows/token-benchmark.yml"
EXPECTED_BADGE_CURRENT="toggle_baseline-control.jsonl"

# extract_step_run_block: print the lines between "- name: <step>" and the
# next "      - name:" (i.e. the next step at the same indent level).
extract_step_run_block() {
    local step_name="$1" file="$2"
    awk -v marker="- name: ${step_name}" '
        index($0, marker) { found=1; next }
        found && /^      - name:/ { exit }
        found { print }
    ' "$file"
}

# check_badge_current_arm: prints one of OK / MISSING_STEP / MISSING_FLAG /
# WRONG_VALUE:<value> for the "Generate Dashboard Files" step in $1.
check_badge_current_arm() {
    local wf="$1"
    local block
    block="$(extract_step_run_block "Generate Dashboard Files" "$wf")"
    if [ -z "$block" ]; then
        echo "MISSING_STEP"
        return
    fi
    local value
    value="$(printf '%s\n' "$block" | grep -oE -- '--badge-current[[:space:]]+\S+' | awk '{print $2}' | head -n1)"
    if [ -z "$value" ]; then
        echo "MISSING_FLAG"
        return
    fi
    if [ "$value" != "$EXPECTED_BADGE_CURRENT" ]; then
        echo "WRONG_VALUE:$value"
        return
    fi
    echo "OK"
}

# POSITIVE: the real, shipped workflow must be pinned correctly.
REAL_RESULT="$(check_badge_current_arm "$WORKFLOW")"
if [ "$REAL_RESULT" = "OK" ]; then
    pass "token-benchmark.yml's 'Generate Dashboard Files' step statically pins --badge-current to the shipped-default guard-off arm ($EXPECTED_BADGE_CURRENT)"
else
    fail "token-benchmark.yml's --badge-current wiring regressed: $REAL_RESULT (expected $EXPECTED_BADGE_CURRENT)"
fi

# NEGATIVE 1: re-pointing the value at the old guard-ON arm (the exact #303
# regression) must be caught.
WRONG_VALUE_WF="$TMPDIR/wrong-value.yml"
sed -E "s/(--badge-current )${EXPECTED_BADGE_CURRENT}/\\1post-control.jsonl/" "$WORKFLOW" > "$WRONG_VALUE_WF"
WRONG_VALUE_RESULT="$(check_badge_current_arm "$WRONG_VALUE_WF")"
if [ "$WRONG_VALUE_RESULT" = "WRONG_VALUE:post-control.jsonl" ]; then
    pass "negative test: re-pointing --badge-current at post-control.jsonl (guard-on arm) is caught"
else
    fail "negative test FAILED to catch --badge-current re-pointed at the guard-on arm (got: $WRONG_VALUE_RESULT)"
fi

# NEGATIVE 2: dropping the flag entirely (falling back to --path, i.e. the
# ORIGINAL #303 bug shape) must also be caught.
MISSING_FLAG_WF="$TMPDIR/missing-flag.yml"
grep -v -- '--badge-current' "$WORKFLOW" > "$MISSING_FLAG_WF"
MISSING_FLAG_RESULT="$(check_badge_current_arm "$MISSING_FLAG_WF")"
if [ "$MISSING_FLAG_RESULT" = "MISSING_FLAG" ]; then
    pass "negative test: dropping --badge-current entirely (falls back to --path's guard-on arm) is caught"
else
    fail "negative test FAILED to catch a dropped --badge-current flag (got: $MISSING_FLAG_RESULT)"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All shipped-default-arm checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
