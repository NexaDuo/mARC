#!/bin/bash
# Regression test for issue #308.
#
# scripts/benchmark_report.py's mad_floor()/pooled_weighted() replace the
# old "gap between two medians" noise floor with a MAD (median absolute
# deviation) over pooled, de-duplicated same-commit observations. The whole
# reason this issue exists: run_token_benchmark.sh:560 does
# `cp post-<task>.jsonl toggle_post-<task>.jsonl` -- arm B is deliberately
# reused as the guard-on side of both the inter-release AND the same-commit
# "causal proof" comparisons, so `post-*.jsonl` and `toggle_post-*.jsonl`
# are byte-identical for every task in the archived run. Any pooling that
# counts files instead of distinct arms double-counts 5 observations as 10.
#
# Offline and deterministic: exercises the archived paid run
# docs/marc/benchmarks/run-34987534132/ (free, no `claude` invocations) plus
# synthetic fixtures for the double-counting trap itself.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ARCHIVE="$REPO_ROOT/docs/marc/benchmarks/run-34987534132"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

[ -d "$ARCHIVE" ] || { echo "FAIL - archived run $ARCHIVE not found"; exit 1; }

# --- 1. Cross-check: the OLD median-vs-median estimator must still be
# reproducible exactly from the archive (33.7169%), so a future change to
# median_weighted() itself doesn't silently drift the numbers this issue's
# analysis was built on.
OLD_ESTIMATOR_PCT="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br
a = br.median_weighted('$ARCHIVE/toggle_baseline-neutral.jsonl')[0]
b = br.median_weighted('$ARCHIVE/toggle_post-neutral.jsonl')[0]
print(f'{abs((a - b) / a * 100):.4f}')
")"
if [ "$OLD_ESTIMATOR_PCT" = "33.7169" ]; then
    pass "old median-vs-median estimator reproduces the shipped 33.7169% exactly from the archive"
else
    fail "old median-vs-median estimator did not reproduce 33.7169% (got $OLD_ESTIMATOR_PCT) -- the pipeline disagrees with production, stop"
fi

# --- 2. mad_floor() over the SAME two files pools by distinct arm: exactly
# 10 observations (5 + 5), never 20, and disagrees with the old estimator
# by an order of magnitude in the intended direction (this is the whole
# point of the issue -- estimator choice moves the floor).
read -r MAD_N MAD_PCT <<< "$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br
floor = br.mad_floor(['$ARCHIVE/toggle_baseline-neutral.jsonl', '$ARCHIVE/toggle_post-neutral.jsonl'])
print(floor.n, f'{floor.mad_pct:.2f}')
")"
if [ "$MAD_N" = "10" ]; then
    pass "mad_floor() pools exactly 10 distinct observations from the archive's neutral same-commit pair (not 20)"
else
    fail "mad_floor() pooled n=$MAD_N observations, expected 10"
fi
# Provisional value (issue #298 owns whether n=5-per-cell can be trusted);
# pinned loosely (range, not exact) so unrelated float-formatting changes
# don't spuriously break this test, while still catching a wrong estimator.
MAD_PCT_INT="${MAD_PCT%.*}"
if [ "$MAD_PCT_INT" -ge 25 ] && [ "$MAD_PCT_INT" -le 38 ] 2>/dev/null; then
    pass "MAD-derived floor over the archived neutral same-commit pair is ${MAD_PCT}% (provisional, issue #298)"
else
    fail "MAD-derived floor ${MAD_PCT}% is outside the expected ~25-38% range for the archived data -- check the estimator"
fi

# --- 3. THE double-counting trap (issue #308's core regression). Build a
# fixture directly mirroring run_token_benchmark.sh:560's `cp` -- an extra
# path whose content is byte-identical to one already pooled -- and assert
# it contributes ZERO extra observations, i.e. is never pooled as if it
# were 5 more independent samples (10 -> 15, or with a 4th duplicate path,
# 10 -> 20).
DEDUP_TEST_OUT="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br

a = '$ARCHIVE/toggle_baseline-neutral.jsonl'
b = '$ARCHIVE/toggle_post-neutral.jsonl'
# post-neutral.jsonl is run_token_benchmark.sh's cp source for
# toggle_post-neutral.jsonl -- byte-identical in the real archive.
c = '$ARCHIVE/post-neutral.jsonl'

n_2files = br.mad_floor([a, b]).n
n_3files_with_dup = br.mad_floor([a, b, c]).n
n_4files_naive = br.mad_floor([a, b, c, a]).n
print(n_2files, n_3files_with_dup, n_4files_naive)
")"
read -r N2 N3 N4 <<< "$DEDUP_TEST_OUT"
if [ "$N2" = "10" ] && [ "$N3" = "10" ] && [ "$N4" = "10" ]; then
    pass "pooling the real archive's byte-identical post-neutral.jsonl/toggle_post-neutral.jsonl pair (and a re-added duplicate) still yields n=10, never 15 or 20 -- the double-counting trap is caught"
else
    fail "double-counting trap NOT caught: pooling 2/3/4 file paths (with duplicates present) yielded n=$N2/$N3/$N4, expected 10/10/10 -- this is the exact bug issue #308 exists to prevent"
fi

# --- 4. Sanity: a fixture with genuinely DIFFERENT (non-duplicate) data in
# each file DOES pool as independent -- de-duplication must not be so
# aggressive it silently drops real, distinct data.
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT
cat > "$TMPDIR/x.jsonl" <<'JSON'
{"weighted": 100}
{"weighted": 200}
JSON
cat > "$TMPDIR/y.jsonl" <<'JSON'
{"weighted": 300}
{"weighted": 400}
JSON
DISTINCT_N="$(python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/scripts')
import benchmark_report as br
print(br.mad_floor(['$TMPDIR/x.jsonl', '$TMPDIR/y.jsonl']).n)
")"
if [ "$DISTINCT_N" = "4" ]; then
    pass "genuinely distinct files pool as independent observations (n=4), de-duplication does not over-collapse real data"
else
    fail "distinct files did not pool independently: got n=$DISTINCT_N, expected 4"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All benchmark_report.py MAD-floor checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
