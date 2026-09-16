#!/bin/bash
# Regression test for issues #291, #304, and #309.
#
# #291: the real three-arm measurement in run_token_benchmark.sh used to be
# reachable ONLY via `[ "$EVENT_NAME" != "release" ]`, i.e. only on a
# `release` event. That event can never fire in this repo: releases are
# published by release.yml using GITHUB_TOKEN, and GitHub does not raise
# workflow events from GITHUB_TOKEN actions (a documented anti-recursion
# safeguard). So the real path was reachable in source but unreachable in
# practice.
#
# #304: the owner decided a paid measurement IS wanted, once per
# minor/major release, never for a patch. Since `release` can never fire,
# the trigger moved to the tag PUSH itself (the same trigger release.yml
# uses to publish), and is_real_run() then keyed the decision on tag SHAPE:
# a 3-component CalVer tag (vYY.M.D) paid; a 4-component tag (vYY.M.D.MICRO)
# stayed free (CHANGELOG.md's own versioning note).
#
# #309: tag shape is not something the operator controls under CalVer -- the
# calendar decides it (whether today's date is already taken as a tag). So
# #304's mechanism could force a genuine release cut onto the free path with
# no way to opt in. Fix: billing now hangs on an explicit, reviewable opt-in
# declaration (OPTIN_FILE, e.g. docs/marc/benchmarks/OPTIN) naming the EXACT
# tag it authorizes; tag shape is demoted from cause-of-spend to a VETO
# (is_patch_tag() can still force free, but can never itself cause paid).
#
# This test is deliberately NOT pinned to "release is broken" (that
# instance). It asserts the durable properties the issues ask for: the
# real-run path has a reachable trigger that does not require the
# never-firing `release` event; a release-shaped tag push with a matching
# opt-in takes the real path and one with no/stale/mismatched opt-in does
# not; a patch tag never pays even with a matching opt-in (the veto holds);
# the opt-in comparison is anchored (no prefix/substring match) and
# whitespace-tolerant; a dispatched run that does not explicitly opt in
# still takes the free stub path; and the workflow's publish steps can never
# independently disagree with is_real_run() because they read its own
# emitted step output rather than re-deriving the decision.
#
# Run this against the pre-#309 script (`git show origin/main~1:scripts/run_token_benchmark.sh`)
# and the opt-in assertions below go RED (every release-shaped tag paid
# unconditionally on tag shape alone, with no opt-in file involved). See the
# PR body for the actual red/green transcript.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="${1:-$SCRIPT_DIR/run_token_benchmark.sh}"
WORKFLOW="${2:-$SCRIPT_DIR/../.github/workflows/token-benchmark.yml}"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# -----------------------------------------------------------------------
# Part 1: the workflow itself must declare triggers that can run the real
# path from the normal tag ritual (a tag push), without depending on the
# `release` event (which never fires, #291), and workflow_dispatch must
# keep its explicit opt-in input so a casual dispatch doesn't silently
# spend money.
#
# `@rev` review on PR #292: the original version of this check was
# `grep -q "workflow_dispatch:"` etc. against the raw file text. `@rev`
# demonstrated that a *commented-out* trigger (every line prefixed with
# `#`, so the substrings are still textually present) still passed — a
# green check produced by dead YAML, on the exact path that has already
# shipped four prior "looked wired, wasn't" defects. A backstop a
# commented-out trigger walks through is worse than none. Fix: parse the
# workflow as structured YAML and assert against the parsed tree.
# -----------------------------------------------------------------------

if [ ! -f "$WORKFLOW" ]; then
    fail "workflow not found at $WORKFLOW"
elif ! python3 -c "import yaml" 2>/dev/null; then
    fail "PyYAML is not available (python3 -c 'import yaml' failed). This test REQUIRES PyYAML to structurally parse token-benchmark.yml -- a raw-text fallback was tried and found foolable (issue #291/PR #292 review: valid YAML that removes real step wiring while leaving the token name present elsewhere as a comment passed a text-based check). Install PyYAML ('pip install pyyaml' / 'python3 -m pip install pyyaml') rather than let this test silently run a weaker check."
else
    if ! python3 - "$WORKFLOW" <<'PYEOF'
import sys

import yaml

path = sys.argv[1]
with open(path) as f:
    text = f.read()

results = []  # (ok: bool, message: str)

def check(ok, msg):
    results.append((ok, msg))

doc = yaml.safe_load(text)
# YAML 1.1 quirk (PyYAML default loader): a bare top-level `on:` key is
# parsed as the boolean True, not the string "on". Check both so this
# doesn't itself become a silent false-negative.
triggers = doc.get("on", doc.get(True))

if isinstance(triggers, dict) and "release" in triggers:
    check(False, "token-benchmark.yml still declares a 'release:' trigger -- issue #304 requires this arm be REMOVED (it can never fire, #291) rather than left disagreeing with the tag-push rule")
else:
    check(True, "token-benchmark.yml declares no 'release:' trigger (issue #304 -- removed, not left dangling)")

push_trigger = triggers.get("push") if isinstance(triggers, dict) else None
push_tags = push_trigger.get("tags") if isinstance(push_trigger, dict) else None
if isinstance(push_tags, list) and any("v*" in str(t) for t in push_tags):
    check(True, f"token-benchmark.yml's push trigger declares a tag pattern (parsed YAML): {push_tags}")
else:
    check(False, "token-benchmark.yml's push trigger has no tag pattern -- the real path has no reachable trigger tied to an actual release tag (issue #304)")

workflow_dispatch = None
if isinstance(triggers, dict):
    workflow_dispatch = triggers.get("workflow_dispatch")

if isinstance(workflow_dispatch, dict):
    check(True, "token-benchmark.yml declares a workflow_dispatch trigger (parsed YAML)")
else:
    check(False, "token-benchmark.yml has no live workflow_dispatch trigger under 'on:' -- the real path would only be reachable via a tag push (issue #291/#304)")
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

# Scoped to actual step structure (env keys / run text of each step), NOT a
# bare "does this token appear anywhere in the file" search -- that bare
# form is exactly what `@rev` demonstrated is foolable by a trailing
# comment elsewhere in the file while the real env wiring is removed.
wired = any(
    "REAL_RUN_INPUT" in (step_env(s) or {}) or "REAL_RUN_INPUT" in step_run_text(s)
    for s in steps
)
if wired:
    check(True, "a step wires the dispatch input through to the script (REAL_RUN_INPUT, parsed YAML)")
else:
    check(False, "no step passes the real_run input to run_token_benchmark.sh as REAL_RUN_INPUT -- the input would be declared but never reach the script")

run_benchmark_step = next((s for s in steps if isinstance(s, dict) and s.get("name") == "Run Token Benchmark"), None)
if run_benchmark_step is not None and run_benchmark_step.get("id") == "run_benchmark":
    check(True, "'Run Token Benchmark' step has id: run_benchmark (issue #304 -- publish steps key off its output)")
else:
    check(False, "'Run Token Benchmark' step is missing id: run_benchmark -- publish steps below cannot reference its output")

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

print("--- workflow structural checks via: PyYAML structural parse ---")
for ok, msg in results:
    print(("ok   - " if ok else "FAIL - ") + msg)

sys.exit(0 if all(ok for ok, _ in results) else 1)
PYEOF
    then
        FAILS=$((FAILS + 1))
    fi
fi

# -----------------------------------------------------------------------
# Part 2: the script's real-vs-stub decision, exercised directly, across
# the full matrix issue #304's acceptance criteria requires: a minor/major
# tag, a patch tag, workflow_dispatch with real_run=true, and
# workflow_dispatch at the default.
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

if ! declare -F emit_real_run_output > /dev/null; then
    fail "emit_real_run_output() is not defined in $TARGET -- the workflow's publish steps have nothing single-sourced to key off of (issue #304)"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

if ! declare -F optin_authorizes > /dev/null; then
    fail "optin_authorizes() is not defined in $TARGET -- issue #309's opt-in declaration has no reachable, testable check"
    echo
    echo "$FAILS check(s) FAILED."
    exit 1
fi

OUTPUT_FILE="$(mktemp)"
OPTIN_DIR="$(mktemp -d)"
trap 'rm -f "$OUTPUT_FILE"; rm -rf "$OPTIN_DIR"' EXIT

# make_optin: write $OPTIN_DIR/$1 with content $2 (verbatim -- callers pass
# whatever whitespace they want to exercise) and echo its path.
make_optin() {
    local content="$2" path="$OPTIN_DIR/$1"
    printf '%s' "$content" > "$path"
    printf '%s' "$path"
}

check_real_run() {
    local desc="$1" expected="$2" event="$3" ref_type="${4:-branch}" ref_name="${5:-main}" real_run_input="${6:-}" optin_file="${7:-$OPTIN_DIR/__nonexistent__}"
    local got got_output
    if EVENT_NAME="$event" REF_TYPE="$ref_type" CURRENT_REF="$ref_name" REAL_RUN_INPUT="$real_run_input" OPTIN_FILE="$optin_file" is_real_run; then
        got="true"
    else
        got="false"
    fi
    if [ "$got" = "$expected" ]; then
        pass "$desc (got: $got)"
    else
        fail "$desc -- expected $expected, got $got"
    fi

    # emit_real_run_output() must agree with is_real_run() for the exact
    # same inputs (issue #304 single-source-of-truth for the workflow's
    # publish gating). It must ALSO write only the hardcoded literal, never
    # the opt-in file's content or the tag (issue #309) -- checked below via
    # the exact-match on "true"/"false".
    : > "$OUTPUT_FILE"
    EVENT_NAME="$event" REF_TYPE="$ref_type" CURRENT_REF="$ref_name" REAL_RUN_INPUT="$real_run_input" OPTIN_FILE="$optin_file" \
        GITHUB_OUTPUT="$OUTPUT_FILE" emit_real_run_output
    got_output="$(grep -o 'real_run=.*' "$OUTPUT_FILE" | cut -d= -f2)"
    if [ "$got_output" = "$expected" ]; then
        pass "$desc -- emit_real_run_output() agrees (real_run=$got_output)"
    else
        fail "$desc -- emit_real_run_output() disagrees with is_real_run(): wrote real_run=$got_output, expected $expected"
    fi
}

# push to a branch (the normal push-to-main CI run) -- stub, unchanged.
check_real_run "push to main (branch) stays on the stub path" false "push" "branch" "main" ""
check_real_run "pull_request event stays on the stub path" false "pull_request" "branch" "main" ""

# push of a TAG -- issue #309's opt-in mechanism. Tag shapes below are drawn
# straight from `git tag --sort=-creatordate` (v26.9.15.1, v26.9.15,
# v26.9.8, v0.28.0, ...) and CHANGELOG.md's versioning note: a 3-component
# CalVer tag is a release, a 4th MICRO component makes it a patch.

MATCH_26_9_16="$(make_optin match_26_9_16 'v26.9.16')"
STALE_26_9_15="$(make_optin stale_26_9_15 'v26.9.15')"
WS_26_9_16="$(make_optin ws_26_9_16 $'  v26.9.16  \n\n')"
PREFIX_26_9_1="$(make_optin prefix_26_9_1 'v26.9.1')"
SUPER_26_9_16_RC="$(make_optin super_26_9_16_rc 'v26.9.16-rc')"

check_real_run "release-shaped tag + matching opt-in takes the real path -- the reachable, operator-controlled trigger issue #309 requires" \
    true "push" "tag" "v26.9.16" "" "$MATCH_26_9_16"
check_real_run "release-shaped tag + NO opt-in file stays on the stub path -- the #304 regression: tag shape alone must no longer pay (issue #309)" \
    false "push" "tag" "v26.9.16" "" "$OPTIN_DIR/__nonexistent__"
check_real_run "release-shaped tag + opt-in naming a DIFFERENT (stale) tag stays on the stub path -- a stale declaration cannot spend (issue #309)" \
    false "push" "tag" "v26.9.16" "" "$STALE_26_9_15"
check_real_run "opt-in with leading/trailing whitespace and a trailing newline still matches its own tag" \
    true "push" "tag" "v26.9.16" "" "$WS_26_9_16"
check_real_run "opt-in that is a PREFIX of the tag (v26.9.1 vs v26.9.16) stays free -- comparison is anchored, not substring" \
    false "push" "tag" "v26.9.16" "" "$PREFIX_26_9_1"
check_real_run "opt-in that is a SUPERSTRING of the tag (v26.9.16-rc vs v26.9.16) stays free -- comparison is anchored, not substring" \
    false "push" "tag" "v26.9.16" "" "$SUPER_26_9_16_RC"
check_real_run "a 4-component PATCH tag (v26.9.15.1) stays free even WITH a matching opt-in -- the tag-shape veto still holds (issue #304, preserved by #309)" \
    false "push" "tag" "v26.9.15.1" "" "$(make_optin match_patch 'v26.9.15.1')"
check_real_run "a legacy 3-component release tag (v0.28.0) with a matching opt-in takes the real path" \
    true "push" "tag" "v0.28.0" "" "$(make_optin match_v0_28_0 'v0.28.0')"

# `release` must no longer be an unconditional real-run arm (removed, not
# left disagreeing with the tag-push rule -- issue #304's explicit AC).
check_real_run "a bare 'release' event (if it ever fired) is NOT treated as real -- the arm was removed, not left inconsistent (issue #304)" false "release" "branch" "main" ""

check_real_run "workflow_dispatch with real_run unset stays on the stub path (safe default)" false "workflow_dispatch" "branch" "main" ""
check_real_run "workflow_dispatch with real_run=false stays on the stub path" false "workflow_dispatch" "branch" "main" "false"
check_real_run "workflow_dispatch with real_run=true takes the real path -- the manual route stays available, unchanged (issue #309 does not touch this)" true "workflow_dispatch" "branch" "main" "true"
check_real_run "workflow_dispatch with real_run=true takes the real path even with an opt-in file present naming something else -- dispatch never consults the opt-in" \
    true "workflow_dispatch" "branch" "main" "true" "$STALE_26_9_15"
check_real_run "pull_request event stays free even with an opt-in file present matching its own ref (e.g. a fork PR editing it) -- only a 'push'+'tag' consults it" \
    false "pull_request" "branch" "v26.9.16" "" "$MATCH_26_9_16"

# -----------------------------------------------------------------------
# Part 3: lockstep check between token-benchmark.yml's publish `if:`
# conditions and "Run Token Benchmark"'s step output (issue #296 review,
# finding 4; re-verified under issue #304's new mechanism).
#
# Both publish steps now read the exact same
# `steps.run_benchmark.outputs.real_run` value -- there is no second,
# independently-maintained expression of "is this a real run" left in the
# workflow to drift from is_real_run(). This asserts that structurally:
# both publish steps exist, declare the IDENTICAL if: condition, and that
# condition is exactly `steps.run_benchmark.outputs.real_run == 'true'`
# (the id checked against Part 1's `run_benchmark_step` assertion above).
# A different shape here (e.g. someone reintroducing a hand-written
# `github.event_name == 'release'` clause) fails loudly.
# -----------------------------------------------------------------------

echo
echo "--- lockstep check: workflow publish if: vs run_benchmark step output (parsed YAML) ---"

if ! python3 - "$WORKFLOW" <<'PYEOF'
import sys

import yaml

workflow_path = sys.argv[1]

FAILS = 0


def check(ok, msg):
    global FAILS
    print(("ok   - " if ok else "FAIL - ") + msg)
    if not ok:
        FAILS += 1


with open(workflow_path) as f:
    doc = yaml.safe_load(f)

jobs = doc.get("jobs", {}) if isinstance(doc, dict) else {}
steps = []
for job in jobs.values():
    if isinstance(job, dict):
        steps.extend(job.get("steps", []) or [])

PUBLISH_STEP_NAMES = ("Generate Dashboard Files", "Commit Dashboard and Benchmarks")
publish_steps = {s.get("name"): s.get("if") for s in steps if isinstance(s, dict) and s.get("name") in PUBLISH_STEP_NAMES}

if set(publish_steps) != set(PUBLISH_STEP_NAMES):
    check(False, f"could not find both publish steps in {workflow_path} (found: {sorted(publish_steps)})")
    sys.exit(1)

EXPECTED = "steps.run_benchmark.outputs.real_run == 'true'"
conditions = {name: str(cond).strip() for name, cond in publish_steps.items()}
distinct = set(conditions.values())

if len(distinct) == 1:
    check(True, "'Generate Dashboard Files' and 'Commit Dashboard and Benchmarks' publish under the IDENTICAL if: condition")
else:
    check(False, f"publish steps have DIFFERENT if: conditions -- they can silently disagree about whether to publish: {conditions}")

for name, cond in conditions.items():
    if cond == EXPECTED:
        check(True, f"'{name}' if: condition reads the run_benchmark step output, not a re-derived expression: {cond!r}")
    else:
        check(False, f"'{name}' if: condition is {cond!r}, expected exactly {EXPECTED!r} -- a hand-written re-derivation here can silently drift from is_real_run() (issue #296/#304)")

sys.exit(1 if FAILS else 0)
PYEOF
then
    FAILS=$((FAILS + 1))
fi

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
