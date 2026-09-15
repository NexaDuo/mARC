#!/bin/bash
# Regression test for issue #295.
#
# A paid run silently lost 3/15 `sweep` measurements: `claude` invocations
# that exited 0 (succeeded, billed) but left no token-telemetry.jsonl file
# behind were reported as "Claude run $i failed with code 0" and lumped in
# with genuine invocation failures. This test sources run_token_benchmark.sh
# (the `[[ "${BASH_SOURCE[0]}" == "${0}" ]]` guard at the bottom means
# main() never runs) and exercises run_claude_safely() directly against a
# fake `claude` binary that is scripted, per invocation, to:
#   1. exit 0 and write telemetry (the ordinary success case)
#   2. exit 0 and NOT write telemetry (the issue #295 instrument-loss case)
#   3. exit 1 (a genuine invocation failure)
#
# Asserts:
#   - the three outcomes are counted separately (ok=1 lost=1 failed=1), not
#     folded into one "skipped" bucket
#   - the instrument-loss case's message is textually distinct from the
#     invocation-failure case's message, and neither says "failed with code
#     0" for the exit-0 case
#   - only the ok run's telemetry line lands in target_file
#   - wait_for_telemetry_file() gives a bounded grace period: a file that
#     appears mid-wait is still picked up as "ok", not "lost" (the flush/
#     race mitigation)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/run_token_benchmark.sh"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# shellcheck source=/dev/null
source "$TARGET"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# -----------------------------------------------------------------------
# Part 1: three-outcome accounting (ok / instrument-loss / invocation-fail)
# -----------------------------------------------------------------------
FAKE_BIN="$WORK/fakebin"
mkdir -p "$FAKE_BIN"
STATE_DIR="$WORK/state"
TARGET_FILE="$WORK/target.jsonl"
RUN_COUNTER_FILE="$WORK/run_counter"
echo 0 > "$RUN_COUNTER_FILE"

# Fake `claude`: run 1 succeeds + writes telemetry; run 2 exits 0 but never
# writes telemetry (the instrument-loss case); run 3 exits 1.
cat > "$FAKE_BIN/claude" << STUB
#!/bin/bash
n=\$(cat "$RUN_COUNTER_FILE")
n=\$((n + 1))
echo "\$n" > "$RUN_COUNTER_FILE"
if [ "\$n" = "1" ]; then
    mkdir -p "\$MARC_STATE_DIR"
    echo '{"session_id": "sess-1", "weighted": 1234}' > "\$MARC_STATE_DIR/token-telemetry.jsonl"
    exit 0
elif [ "\$n" = "2" ]; then
    exit 0
else
    exit 7
fi
STUB
chmod +x "$FAKE_BIN/claude"

OUT="$WORK/run.log"
PATH="$FAKE_BIN:$PATH" run_claude_safely "irrelevant prompt" "$TARGET_FILE" "$STATE_DIR" 3 "test-label" > "$OUT" 2>&1

if grep -q "INSTRUMENT LOSS" "$OUT" && grep -q "FAILED: nonzero exit code 7" "$OUT"; then
    pass "instrument-loss (exit 0, no telemetry) and invocation-failure (nonzero exit) produce textually distinct messages"
else
    fail "did not find both distinct messages in output: $(cat "$OUT")"
fi

if grep -q "failed with code 0" "$OUT"; then
    fail "regression: an exit-0 run is still reported as 'failed with code 0' (issue #295)"
else
    pass "an exit-0 run is never reported as 'failed with code 0'"
fi

if grep -q "ok=1 lost=1(instrument) failed=1(invocation) -- requested n=3" "$OUT"; then
    pass "per-cell summary counts ok/lost/failed separately (1/1/1 of 3 requested)"
else
    fail "per-cell summary line missing or wrong: $(cat "$OUT")"
fi

if [ -f "$TARGET_FILE" ] && [ "$(wc -l < "$TARGET_FILE")" -eq 1 ] && grep -q "sess-1" "$TARGET_FILE"; then
    pass "only the genuinely-ok run's telemetry line lands in the target file"
else
    fail "target file has unexpected content: $(cat "$TARGET_FILE" 2>/dev/null || echo '<missing>')"
fi

# -----------------------------------------------------------------------
# Part 2: wait_for_telemetry_file() bounded-wait mitigation for the
# suspected flush/timeout race (issue #295 AC4). A file that appears
# shortly after `claude` returns (simulating the Stop hook still finishing
# its write) must still be picked up as "ok", not declared lost.
# -----------------------------------------------------------------------
DELAYED_FILE="$WORK/delayed/token-telemetry.jsonl"
mkdir -p "$(dirname "$DELAYED_FILE")"
( sleep 1; mkdir -p "$(dirname "$DELAYED_FILE")"; echo '{"session_id": "delayed"}' > "$DELAYED_FILE" ) &
BGPID=$!
if wait_for_telemetry_file "$DELAYED_FILE" 5 1; then
    pass "wait_for_telemetry_file() picks up a file that appears mid-wait (flush-race mitigation)"
else
    fail "wait_for_telemetry_file() did not pick up a file that appeared within the bounded wait window"
fi
wait "$BGPID" 2>/dev/null || true

NEVER_FILE="$WORK/never/token-telemetry.jsonl"
if wait_for_telemetry_file "$NEVER_FILE" 2 0; then
    fail "wait_for_telemetry_file() reported success for a file that never appears"
else
    pass "wait_for_telemetry_file() correctly gives up (returns failure) for a file that never appears"
fi

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
