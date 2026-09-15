#!/bin/bash
# Regression test for issue #295 (AC2/AC3).
#
# scripts/benchmark_report.py must surface each (task, arm) cell's sample
# count against the expected ITERATIONS, and must fail loudly (non-zero
# exit) rather than silently reporting a median over a short cell, when
# --iterations is given. Two parts:
#
#   1. Against the ARCHIVED run docs/marc/benchmarks/run-34987534132/
#      (free, no `claude` invocations): that run's own `sweep` task lost
#      samples (post-sweep.jsonl has 4/5, toggle_baseline-sweep.jsonl has
#      3/5) -- exactly the loss issue #295 reports. With --iterations 5 the
#      report must show n=4/5 and n=3/5, flag the 3/5 cell UNTRUSTWORTHY,
#      and exit non-zero.
#   2. Without --iterations (today's stub-path behavior, and the old CLI
#      surface), the same archived data must NOT fail -- this is the
#      backward-compatible free/informational path, never gated.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPT="$REPO_ROOT/scripts/benchmark_report.py"
ARCHIVE="$REPO_ROOT/docs/marc/benchmarks/run-34987534132"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

[ -d "$ARCHIVE" ] || { echo "FAIL - archived run $ARCHIVE not found"; exit 1; }

TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

OUT="$TMPDIR/report.txt"
set +e
python3 "$SCRIPT" --task-names-file task_names.txt --dir "$ARCHIVE" --iterations 5 > "$OUT" 2>&1
EXIT_CODE=$?
set -e

if [ "$EXIT_CODE" -ne 0 ]; then
    pass "report exits non-zero (fails loudly) when the archived run's known short cell (sweep, 3/5) is present"
else
    fail "report exited 0 despite a 3/5 (untrustworthy) cell in the archived data"
fi

if grep -q "n=4/5" "$OUT" && grep -q "n=3/5" "$OUT"; then
    pass "report surfaces the actual sample count against ITERATIONS for the short cells (n=4/5, n=3/5)"
else
    fail "report did not surface the expected short-cell counts: $(cat "$OUT")"
fi

if grep -q "UNTRUSTWORTHY" "$OUT"; then
    pass "report marks the 3/5 sweep cell UNTRUSTWORTHY"
else
    fail "report did not mark the 3/5 cell UNTRUSTWORTHY: $(cat "$OUT")"
fi

if grep -q "SHORT" "$OUT"; then
    pass "report flags the 4/5 sweep cell as SHORT (below ITERATIONS but above the untrustworthy threshold)"
else
    fail "report did not flag the 4/5 cell as SHORT: $(cat "$OUT")"
fi

# Full-strength cells (control, neutral: 5/5 everywhere) must NOT be flagged.
CONTROL_LINE="$(grep '^control' "$OUT" | head -n1)"
if echo "$CONTROL_LINE" | grep -q "UNTRUSTWORTHY\|SHORT"; then
    fail "a full-strength (5/5) cell was incorrectly flagged: $CONTROL_LINE"
else
    pass "a full-strength (5/5) cell is not flagged"
fi

# --- Backward compatibility: without --iterations, the same archived data
# (which includes the short cells) must NOT fail -- this is the existing,
# informational-only behavior relied on by the free stub path.
NO_ITER_OUT="$TMPDIR/no-iter-report.txt"
if python3 "$SCRIPT" --task-names-file task_names.txt --dir "$ARCHIVE" > "$NO_ITER_OUT" 2>&1; then
    pass "without --iterations, the same data does not fail (backward compatible with the stub path)"
else
    fail "without --iterations, the report unexpectedly failed: $(cat "$NO_ITER_OUT")"
fi
if grep -q "UNTRUSTWORTHY\|SHORT" "$NO_ITER_OUT"; then
    fail "without --iterations, cells were flagged SHORT/UNTRUSTWORTHY (should be silent, informational-only n=X)"
else
    pass "without --iterations, no SHORT/UNTRUSTWORTHY flags are printed (unchanged n=X display)"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All sample-count checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
