#!/bin/bash
# Regression test for issue #291.
#
# The real three-arm measurement in run_token_benchmark.sh used to be
# reachable ONLY via `[ "$EVENT_NAME" != "release" ]`, i.e. only on a
# `release` event. That event can never fire in this repo: releases are
# published by release.yml using GITHUB_TOKEN, and GitHub does not raise
# workflow events from GITHUB_TOKEN actions (a documented anti-recursion
# safeguard). So the real path was reachable in source but unreachable in
# practice — the fourth/fifth layer of the same class of bug (#285, #287
# x2, this one).
#
# This test is deliberately NOT pinned to "release is broken" (that
# instance). It asserts the more durable property the issue asks for: the
# real-run path has SOME reachable trigger that does not require cutting a
# release, and a dispatched run that does not explicitly opt in still takes
# the free stub path (so a bare "add a trigger" fix, which would silently
# stub every dispatched run, still fails this test).
#
# Run this against the pre-fix script (`git show origin/main:scripts/run_token_benchmark.sh`)
# and it is RED: `is_real_run` does not exist there at all. Post-fix it is
# GREEN. See the PR body for the actual red/green transcript.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$SCRIPT_DIR/run_token_benchmark.sh}"
WORKFLOW="${2:-$SCRIPT_DIR/../.github/workflows/token-benchmark.yml}"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# -----------------------------------------------------------------------
# Part 1: the workflow itself must declare a trigger that can run the real
# path without a release (workflow_dispatch, with an explicit opt-in input
# so a casual dispatch doesn't silently spend money).
# -----------------------------------------------------------------------

if [ ! -f "$WORKFLOW" ]; then
    fail "workflow not found at $WORKFLOW"
else
    if grep -q "workflow_dispatch:" "$WORKFLOW"; then
        pass "token-benchmark.yml declares a workflow_dispatch trigger"
    else
        fail "token-benchmark.yml has no workflow_dispatch trigger -- the real path is still reachable only via 'release', which never fires (issue #291)"
    fi

    if grep -q "real_run:" "$WORKFLOW"; then
        pass "workflow_dispatch declares a 'real_run' input to opt into the paid measurement"
    else
        fail "workflow_dispatch has no explicit opt-in input -- a dispatched run would either always stub (unreachable) or always spend money (unsafe)"
    fi

    if grep -q "REAL_RUN_INPUT" "$WORKFLOW"; then
        pass "the 'Run Token Benchmark' step wires the dispatch input through to the script (REAL_RUN_INPUT)"
    else
        fail "the workflow does not pass the real_run input to run_token_benchmark.sh -- the input would be declared but never reach the script"
    fi

    if grep -q "actions/upload-artifact" "$WORKFLOW" && grep -qE "if:\s*always\(\)" "$WORKFLOW"; then
        pass "workflow uploads the raw measurement as an artifact unconditionally (if: always())"
    else
        fail "workflow does not unconditionally upload the raw measurement as an artifact -- a failed publish would destroy a paid-for measurement again"
    fi

    # The artifact upload step must appear BEFORE any commit/push step, so a
    # failure in the commit/push step cannot prevent the upload.
    upload_line=$(grep -n "actions/upload-artifact" "$WORKFLOW" | head -1 | cut -d: -f1 || true)
    push_line=$(grep -n "git push" "$WORKFLOW" | head -1 | cut -d: -f1 || true)
    if [ -n "$upload_line" ] && [ -n "$push_line" ] && [ "$upload_line" -lt "$push_line" ]; then
        pass "artifact upload step is ordered before the commit/push step"
    else
        fail "artifact upload step is not clearly ordered before the commit/push step (upload_line=$upload_line push_line=$push_line)"
    fi
fi

# -----------------------------------------------------------------------
# Part 2: the script's real-vs-stub decision, exercised directly.
# -----------------------------------------------------------------------

if [ ! -f "$TARGET" ]; then
    fail "script not found at $TARGET"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

# shellcheck source=/dev/null
if ! source "$TARGET" 2>/tmp/test_benchmark_dispatch_reachable.source.err; then
    fail "could not source $TARGET (see /tmp/test_benchmark_dispatch_reachable.source.err)"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

if ! declare -F is_real_run > /dev/null; then
    fail "is_real_run() is not defined in $TARGET -- the real/stub decision is not an independently testable, reachable trigger (issue #291)"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

check_real_run() {
    local desc="$1" expected="$2" event="$3" real_run_input="${4:-}"
    local got
    if EVENT_NAME="$event" REAL_RUN_INPUT="$real_run_input" is_real_run; then
        got="true"
    else
        got="false"
    fi
    if [ "$got" = "$expected" ]; then
        pass "$desc (got: $got)"
    else
        fail "$desc -- expected $expected, got $got"
    fi
}

check_real_run "push event stays on the stub path" false "push" ""
check_real_run "pull_request event stays on the stub path" false "pull_request" ""
check_real_run "release event takes the real path" true "release" ""
check_real_run "workflow_dispatch with real_run unset stays on the stub path (safe default)" false "workflow_dispatch" ""
check_real_run "workflow_dispatch with real_run=false stays on the stub path" false "workflow_dispatch" "false"
check_real_run "workflow_dispatch with real_run=true takes the real path -- THE reachable trigger this issue requires" true "workflow_dispatch" "true"

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
