#!/bin/bash
# Regression test for issue #285.
#
# generate_telemetry_dashboard.py's generate_badge() used to run only when
# --compare was passed, and the release step in
# .github/workflows/token-benchmark.yml invoked the script with
# `--path post.jsonl` alone (no comparison data). Result: a real release
# would regenerate docs/marc/telemetry.md with real chart data while
# docs/marc/telemetry-badge.json stayed frozen forever — a reader would see
# "No Data" on the README badge next to a populated dashboard chart, which
# is its own kind of misleading (found by `@rev` reviewing PR #284).
#
# The fix makes generate_badge() unconditional (always called from main(),
# from the SAME `sessions` object the markdown was just written from) and
# has the release step pass --baseline so the badge has real comparison
# data on every release. This test asserts both halves of that guarantee:
#
#   1. Static: the release-only "Generate Dashboard Files" step actually
#      wires --baseline through, so the badge computation has real inputs
#      on every release (not just the chart).
#   2. Dynamic: one invocation of generate_telemetry_dashboard.py, given
#      real (non-empty, differing) baseline/current fixture data, produces
#      BOTH a populated telemetry.md chart AND a non-"No Data" badge —
#      proving the two outputs are generated from the same run and cannot
#      diverge.
#
# This does NOT exercise the real `release`-event pipeline (needs a real
# tag, the claude CLI, and credentials) — it exercises the same script and
# the same workflow wiring the release path uses, offline and
# deterministically.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WORKFLOW="${1:-$REPO_ROOT/.github/workflows/token-benchmark.yml}"
SCRIPT="$REPO_ROOT/scripts/generate_telemetry_dashboard.py"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# --- 1. Static check -------------------------------------------------------
# Same step-splitting approach as scripts/test_token_benchmark_no_pr_comment.sh
# (no PyYAML dependency): find the "Generate Dashboard Files" step's own text
# and confirm it passes --baseline alongside --path.
set +e
STEP_TEXT="$(python3 - "$WORKFLOW" <<'PYEOF'
import re
import sys

path = sys.argv[1]
with open(path) as f:
    text = f.read()

step_starts = [m.start() for m in re.finditer(r"^\s*- name:", text, re.MULTILINE)]
step_starts.append(len(text))
for i in range(len(step_starts) - 1):
    chunk = text[step_starts[i]:step_starts[i + 1]]
    first_line = chunk.splitlines()[0] if chunk.splitlines() else ""
    if "Generate Dashboard Files" in first_line:
        print(chunk)
        break
PYEOF
)"
PY_STATUS=$?
set -e

if [ "$PY_STATUS" -ne 0 ] || [ -z "$STEP_TEXT" ]; then
    fail "could not locate the 'Generate Dashboard Files' step in $WORKFLOW"
elif ! echo "$STEP_TEXT" | grep -q "generate_telemetry_dashboard.py"; then
    fail "'Generate Dashboard Files' step no longer calls generate_telemetry_dashboard.py"
elif ! echo "$STEP_TEXT" | grep -q -- "--baseline"; then
    fail "'Generate Dashboard Files' step does not pass --baseline — the badge would have no real comparison data on release"
else
    pass "'Generate Dashboard Files' step passes --baseline to generate_telemetry_dashboard.py"
fi

# --- 2. Dynamic check -------------------------------------------------------
TMPDIR="$(mktemp -d)"
trap 'rm -rf "$TMPDIR"' EXIT

cat > "$TMPDIR/baseline.jsonl" <<'JSON'
{"session_id": "sess-fixture-base-1", "weighted": 20000, "turns": 4, "model": "fixture-model", "ts": 1800000000}
JSON
cat > "$TMPDIR/post.jsonl" <<'JSON'
{"session_id": "sess-fixture-post-1", "weighted": 9000, "turns": 4, "model": "fixture-model", "ts": 1800000500}
JSON

MD_OUT="$TMPDIR/telemetry.md"
BADGE_OUT="$TMPDIR/telemetry-badge.json"
STDERR_LOG="$TMPDIR/stderr.log"

set +e
python3 "$SCRIPT" \
    --path "$TMPDIR/post.jsonl" \
    --baseline "$TMPDIR/baseline.jsonl" \
    --md-out "$MD_OUT" \
    --badge-out "$BADGE_OUT" 2>"$STDERR_LOG"
RUN_STATUS=$?
set -e

if [ "$RUN_STATUS" -ne 0 ]; then
    fail "generate_telemetry_dashboard.py exited $RUN_STATUS with --path/--baseline (same flags the release step now uses): $(cat "$STDERR_LOG")"
else
    if [ -f "$MD_OUT" ] && grep -q "xychart-beta" "$MD_OUT" && grep -q "sess-fix" "$MD_OUT"; then
        pass "telemetry.md was regenerated with the fixture's real session data"
    else
        fail "telemetry.md was not regenerated with real fixture data"
    fi

    if [ -f "$BADGE_OUT" ] && grep -q '"message"' "$BADGE_OUT" && ! grep -q "No Data" "$BADGE_OUT"; then
        pass "telemetry-badge.json was regenerated with a real percentage in the same run as telemetry.md"
    else
        fail "telemetry-badge.json was NOT regenerated with real data (stayed 'No Data' or missing) even though telemetry.md got a real chart — badge/dashboard divergence"
    fi
fi

# --- 3. Honest-empty-state check -------------------------------------------
# When there genuinely is no baseline to compare against, the badge must
# still read "No Data"/inactive — never a fabricated or stale percentage
# (PR #279's fix must not regress).
NO_BASELINE_BADGE_OUT="$TMPDIR/no-baseline-badge.json"
set +e
python3 "$SCRIPT" \
    --path "$TMPDIR/post.jsonl" \
    --md-out "$TMPDIR/no-baseline-telemetry.md" \
    --badge-out "$NO_BASELINE_BADGE_OUT" 2>"$TMPDIR/stderr2.log"
NO_BASELINE_STATUS=$?
set -e

if [ "$NO_BASELINE_STATUS" -ne 0 ]; then
    fail "generate_telemetry_dashboard.py exited $NO_BASELINE_STATUS without --baseline: $(cat "$TMPDIR/stderr2.log")"
elif [ -f "$NO_BASELINE_BADGE_OUT" ] && grep -q '"No Data"' "$NO_BASELINE_BADGE_OUT" && grep -q '"inactive"' "$NO_BASELINE_BADGE_OUT"; then
    pass "badge stays honest 'No Data'/inactive when no baseline is given"
else
    fail "badge did not fall back to honest 'No Data'/inactive when no baseline was given"
fi

echo "---"
if [ "$FAILS" -eq 0 ]; then
    echo "All telemetry badge/dashboard same-run checks passed."
    exit 0
else
    echo "$FAILS check(s) failed."
    exit 1
fi
