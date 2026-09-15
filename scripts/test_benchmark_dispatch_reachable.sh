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
#
# `@rev` review on PR #292: the original version of this check was
# `grep -q "workflow_dispatch:"` etc. against the raw file text. `@rev`
# demonstrated that a *commented-out* trigger (every line prefixed with
# `#`, so the substrings are still textually present) still passed — a
# green check produced by dead YAML, on the exact path that has already
# shipped four prior "looked wired, wasn't" defects. A backstop a
# commented-out trigger walks through is worse than none.
#
# Fix: parse the workflow as structured YAML and assert against the parsed
# tree (a `workflow_dispatch:` key literally does not exist in the parsed
# document if it's commented out — there is no text-matching hole left).
# PyYAML is present in this dev environment but its availability on the CI
# runner's bare system python3 is NOT guaranteed (see
# scripts/test_token_benchmark_no_pr_comment.sh's own comment on this same
# point). So: try a real YAML parse first; if PyYAML is unavailable, fall
# back to a comment-aware structural scan (strip full-comment lines, then
# require the trigger key at the correct top-level indent) rather than
# silently degrading to the same raw-text grep that just failed review.
# -----------------------------------------------------------------------

if [ ! -f "$WORKFLOW" ]; then
    fail "workflow not found at $WORKFLOW"
else
    if ! python3 - "$WORKFLOW" <<'PYEOF'
import re
import sys

path = sys.argv[1]
with open(path) as f:
    text = f.read()

results = []  # (ok: bool, message: str)

def check(ok, msg):
    results.append((ok, msg))

try:
    import yaml
    USED = "PyYAML structural parse"

    doc = yaml.safe_load(text)
    # YAML 1.1 quirk (PyYAML default loader): a bare top-level `on:` key is
    # parsed as the boolean True, not the string "on". Check both so this
    # doesn't itself become a silent false-negative.
    triggers = doc.get("on", doc.get(True))

    workflow_dispatch = None
    if isinstance(triggers, dict):
        workflow_dispatch = triggers.get("workflow_dispatch")

    if isinstance(workflow_dispatch, dict):
        check(True, "token-benchmark.yml declares a workflow_dispatch trigger (parsed YAML)")
    else:
        check(False, "token-benchmark.yml has no live workflow_dispatch trigger under 'on:' -- the real path is still reachable only via 'release', which never fires (issue #291)")
        workflow_dispatch = {}

    inputs = workflow_dispatch.get("inputs", {}) if isinstance(workflow_dispatch, dict) else {}
    if isinstance(inputs, dict) and "real_run" in inputs:
        check(True, "workflow_dispatch declares a 'real_run' input to opt into the paid measurement (parsed YAML)")
    else:
        check(False, "workflow_dispatch has no 'real_run' input -- a dispatched run would either always stub (unreachable) or always spend money (unsafe)")

    jobs = doc.get("jobs", {}) if isinstance(doc, dict) else {}
    steps = []
    for job in jobs.values():
        if isinstance(job, dict):
            steps.extend(job.get("steps", []) or [])

    def step_run_text(step):
        return step.get("run", "") if isinstance(step, dict) else ""

    def step_uses(step):
        return step.get("uses", "") if isinstance(step, dict) else ""

    def step_env(step):
        return step.get("env", {}) if isinstance(step, dict) else {}

    wired = any(
        "REAL_RUN_INPUT" in (step_env(s) or {}) or "REAL_RUN_INPUT" in step_run_text(s)
        for s in steps
    )
    if wired:
        check(True, "a step wires the dispatch input through to the script (REAL_RUN_INPUT, parsed YAML)")
    else:
        check(False, "no step passes the real_run input to run_token_benchmark.sh as REAL_RUN_INPUT -- the input would be declared but never reach the script")

    upload_idx = next((i for i, s in enumerate(steps) if "actions/upload-artifact" in step_uses(s)), None)
    if upload_idx is not None:
        upload_step = steps[upload_idx]
        if_cond = str(upload_step.get("if", "")).strip()
        if if_cond == "always()":
            check(True, "workflow uploads the raw measurement as an artifact unconditionally (if: always(), parsed YAML)")
        else:
            check(False, f"artifact-upload step exists but its 'if:' is not exactly 'always()' (got: {if_cond!r}) -- a failed publish could destroy a paid-for measurement again")
    else:
        check(False, "no step uses actions/upload-artifact -- a failed publish would destroy a paid-for measurement again")

    push_idx = next((i for i, s in enumerate(steps) if "git push" in step_run_text(s)), None)
    if upload_idx is not None and push_idx is not None and upload_idx < push_idx:
        check(True, "artifact upload step is ordered before the commit/push step (parsed step order)")
    else:
        check(False, f"artifact upload step is not ordered before the commit/push step (upload_idx={upload_idx} push_idx={push_idx})")

except ImportError:
    USED = "comment-aware structural fallback (PyYAML unavailable)"

    # Strip full-comment lines (leading '#', any indent) before scanning, so
    # a commented-out trigger's substrings do not survive into the checks
    # below -- this is the specific hole `@rev` found in the old plain-grep
    # version.
    live_lines = [ln for ln in text.splitlines() if not re.match(r'^\s*#', ln)]
    live_text = "\n".join(live_lines)

    on_match = re.search(r'^on:\s*$', live_text, re.MULTILINE)
    if on_match:
        # Slice from the 'on:' block to the next top-level (col-0) key.
        rest = live_text[on_match.end():]
        next_top = re.search(r'^\S', rest, re.MULTILINE)
        on_block = rest[: next_top.start()] if next_top else rest
    else:
        on_block = ""

    wd_match = re.search(r'^  workflow_dispatch:\s*$', on_block, re.MULTILINE)
    if wd_match:
        check(True, "token-benchmark.yml declares a workflow_dispatch trigger (comment-aware scan)")
        wd_rest = on_block[wd_match.end():]
        next_same_or_lower = re.search(r'^  \S', wd_rest, re.MULTILINE)
        wd_block = wd_rest[: next_same_or_lower.start()] if next_same_or_lower else wd_rest
    else:
        check(False, "token-benchmark.yml has no live (non-commented) workflow_dispatch trigger under 'on:' -- the real path is still reachable only via 'release', which never fires (issue #291)")
        wd_block = ""

    if re.search(r'real_run:\s*$', wd_block, re.MULTILINE):
        check(True, "workflow_dispatch declares a 'real_run' input (comment-aware scan)")
    else:
        check(False, "workflow_dispatch has no live 'real_run' input -- a dispatched run would either always stub (unreachable) or always spend money (unsafe)")

    if "REAL_RUN_INPUT" in live_text:
        check(True, "a step wires the dispatch input through to the script (REAL_RUN_INPUT, comment-aware scan)")
    else:
        check(False, "no live reference to REAL_RUN_INPUT -- the real_run input would be declared but never reach the script")

    live_lines_enum = list(enumerate(live_lines))
    upload_line = next((i for i, ln in live_lines_enum if "actions/upload-artifact" in ln), None)
    always_line = next((i for i, ln in live_lines_enum if re.search(r'if:\s*always\(\)', ln)), None)
    if upload_line is not None and always_line is not None:
        check(True, "workflow uploads the raw measurement as an artifact unconditionally (if: always(), comment-aware scan)")
    else:
        check(False, "no live, unconditional (if: always()) artifact-upload step found -- a failed publish could destroy a paid-for measurement again")

    push_line = next((i for i, ln in live_lines_enum if "git push" in ln), None)
    if upload_line is not None and push_line is not None and upload_line < push_line:
        check(True, "artifact upload step is ordered before the commit/push step (comment-aware scan)")
    else:
        check(False, f"artifact upload step is not ordered before the commit/push step (upload_line={upload_line} push_line={push_line})")

print(f"--- workflow structural checks via: {USED} ---")
for ok, msg in results:
    print(("ok   - " if ok else "FAIL - ") + msg)

sys.exit(0 if all(ok for ok, _ in results) else 1)
PYEOF
    then
        FAILS=$((FAILS + 1))
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
