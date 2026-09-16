#!/bin/bash
set -eo pipefail

EVENT_NAME="${GITHUB_EVENT_NAME:-push}"
CURRENT_REF="${GITHUB_REF_NAME:-main}"
# "branch" or "tag" -- distinguishes a `push` to main (GITHUB_REF_NAME=="main")
# from a `push` of a tag (GITHUB_REF_NAME=="v26.9.15"), which both set
# EVENT_NAME to "push". See is_real_run() below (issue #304).
REF_TYPE="${GITHUB_REF_TYPE:-branch}"
GITHUB_WORKSPACE="${GITHUB_WORKSPACE:-$PWD}"
# Set by the workflow from the `real_run` workflow_dispatch input (issue
# #291). A dispatched run defaults to the stub path unless explicitly asked
# for the real (paid, multi-`claude`-invocation) measurement — see
# is_real_run() below.
REAL_RUN_INPUT="${REAL_RUN_INPUT:-false}"

MODEL="claude-sonnet-5"

# --- Task set (issue #275) ---------------------------------------------------
# The single hardcoded board.py task measured exactly one workload shape: a
# large single-file read where the work IS the read, so a read-guard (which
# only forces targeted reads/grep instead of one big Read) has no cheaper path
# available and can only lose. Run 34961492007 confirmed that (guard cost
# 3.1x more on that task) and confirmed the harness itself is sound (two
# unguarded arms, different releases, 2% apart) — the missing piece was task
# diversity, not instrument validity.
#
# Three tasks, three different shapes:
#   control  - VERBATIM the original task. A calibrated control with known
#              sign/magnitude (guard loses, 3.1x, run 34961492007). Kept
#              unchanged so this run stays comparable to that measurement.
#   sweep    - Chosen to plausibly favor bulk-I/O offloading (i.e. hurt the
#              read-guard less, or not at all): it requires visiting all 10
#              non-test core/scripts/*.py files (3346 lines total), but the
#              information needed (top-level `def` lines) is a sparse
#              fraction of that content, and 3 of the 10 files exceed the
#              350-line guard threshold (dispatch_agent.py, token_sentinel.py,
#              board.py). A full-Read-everything approach pays for all 3346
#              lines; a grep-for-`^def`/targeted-read approach (which the
#              guard forces on the 3 large files, and which the agent may
#              reach for anyway even unguarded) pays for a much smaller
#              fraction. This is a genuine hypothesis, not a certainty — grep
#              is unaffected by the guard either way, so if the model already
#              defaults to grep for this shape of task regardless of arm,
#              this task will read as "neutral" rather than "guard wins", and
#              that itself would be a real, reportable finding (see PR body).
#   neutral  - AGENTS.md (142 lines), comfortably under the 350-line
#              threshold used by arm B. The guard structurally cannot fire on
#              it in either configured arm, so any difference this task shows
#              between arm B and arm C is pure run-to-run variance, not a
#              guard effect — a sanity check on the other two tasks' deltas.
TASK_NAMES=(control sweep neutral)
TASK_PROMPTS=(
    "read core/scripts/board.py and output a summary"
    "List every top-level (module-level) function definition across all non-test .py files in core/scripts/ (skip any file whose name starts with test_). Format each as '<filename>: <function_name>(...)'. Do not include methods defined inside classes, or functions nested inside other functions."
    "read AGENTS.md and summarize its \"Operating principles\" section in 3 bullet points"
)

# Iterations per (task, arm). Run 34961492007's single arm C ranged 14,600 /
# 14,630 / 5,742 weighted tokens across 3 runs of the IDENTICAL task+arm — a
# 2.5x spread from n=3 alone. n=3 cannot distinguish a real effect that size
# from noise. Raising to n=5 and switching SUM -> MEDIAN (scripts/
# benchmark_report.py) means one outlier run (like that 5,742) no longer
# drags or dominates the aggregate the way summing 3 values does; the median
# of 5 needs 3 of 5 runs to agree before it moves. n=5 (not more) is a
# deliberate cost tradeoff — see the PR body for the full invocation-count
# and cost accounting across 3 tasks x 3 arms x 5 iterations.
ITERATIONS=5

# --- Testable helpers (defined before main(); see the sourcing guard at the
# bottom of this file). Keeping these as standalone functions lets a companion
# test source this script (which then does NOT execute main()) and exercise
# them directly with stubbed `claude`/`python3` state, without a real release
# environment (tag, CLI, credentials). ---------------------------------------

# resolve_claude_version: capture `claude --version`. MUST be called after the
# CLI is actually installed (see main(), below) — a failure here means the
# CLI is genuinely missing/broken post-install, which is a real environment
# error. We deliberately do NOT swallow it into a placeholder like "unknown":
# doing so would silently defeat the drift-guard hash that depends on this
# value (issue #281).
resolve_claude_version() {
    claude --version 2>/dev/null || {
        echo "Error: 'claude --version' failed after CLI install. Refusing to hash a placeholder 'unknown' version — this would silently disable the drift guard." >&2
        return 1
    }
}

# task_set_blob: pure function, no side effects. Deterministically serializes
# the full task set (name=prompt pairs, in array order) into a single string
# suitable for hashing. Separated from compute_task_hash so the hash itself
# stays a trivial, directly-testable function over a plain string, while this
# is the one place that knows how to flatten the TASK_NAMES/TASK_PROMPTS
# arrays (issue #275: the old single-task hash covered only one TASK string
# and would not have detected a change to any task other than the first).
task_set_blob() {
    local i blob=""
    for i in "${!TASK_NAMES[@]}"; do
        blob+="${TASK_NAMES[$i]}=${TASK_PROMPTS[$i]}|"
    done
    printf '%s' "$blob"
}

# compute_task_hash: pure function, no side effects. `tasks_blob` is expected
# to be the output of task_set_blob() (or an equivalent literal, as the
# companion test passes for reproducibility) rather than a single task
# string — this is the issue #275 fix to the drift guard, which previously
# hashed only the one hardcoded TASK and would not notice a change to the
# task SET (add/remove/reorder a task, change iteration count) as drift.
compute_task_hash() {
    local claude_version="$1" model="$2" tasks_blob="$3" iterations="$4"
    echo -n "$claude_version:$model:$tasks_blob:$iterations" | sha256sum | awk '{print $1}' | cut -c 1-8
}

# ensure_marketplace_added: idempotent `claude plugin marketplace add`.
#
# `claude plugin marketplace add "$source"` is called once per arm (arm A,
# inside the ../arm-a worktree, and arm B/C in the current workspace) because
# each arm needs the marketplace re-pointed at ITS OWN checkout — a blind
# `|| true` (like the neighbouring `git worktree add ... || true`) would risk
# masking a genuine failure (network, bad path, permissions) on the second
# call, and would still leave the marketplace pointed at the wrong source if
# the CLI's add-on-existing-name behavior ever isn't an upsert.
#
# Instead: check `claude plugin marketplace list --json` for an entry with
# this name BEFORE adding. If one exists, remove it explicitly so the
# subsequent `add` is guaranteed to (re)point the name at $source rather than
# relying on undocumented add-when-exists semantics.
#
# IMPORTANT (issue #281 PR #283 review, `@rev` finding, HIGH): `claude plugin
# marketplace list --json` is captured into a variable with `|| echo '[]'`
# BEFORE it is ever piped into python3, specifically so its own exit status
# never takes part in the `set -eo pipefail` pipeline. An empty/failing list
# on the very first call (nothing registered yet, e.g. arm A's first-ever
# call) must read as an ordinary "not registered" outcome, not a fatal error
# that kills the whole release before it ever reaches `add`. Genuine `remove`/
# `add` failures still propagate normally (no `|| true` around those).
#
# Also (`@sec` finding, LOW, open point resolved): if `remove` succeeds but
# the following `add` then fails (transient network error, bad path), the
# entry would otherwise be left deleted — worse than the old code's failure
# mode, which at least left the stale-but-valid prior entry intact. We
# snapshot the previous entry's source before removing it and, if the re-add
# fails, attempt to restore that previous registration before propagating the
# original failure.
ensure_marketplace_added() {
    local source="$1"
    local name
    name="$(python3 -c "
import json, sys
print(json.load(open(sys.argv[1] + '/.claude-plugin/marketplace.json'))['name'])
" "$source")" || {
        echo "Error: could not read marketplace name from $source/.claude-plugin/marketplace.json" >&2
        return 1
    }

    local list_json
    list_json="$(claude plugin marketplace list --json 2>/dev/null || echo '[]')"
    [ -n "$list_json" ] || list_json='[]'

    local existing_source
    if existing_source="$(python3 -c "
import json, sys
list_json, name = sys.argv[1], sys.argv[2]
try:
    entries = json.loads(list_json)
except Exception:
    entries = []
for e in entries:
    if e.get('name') == name:
        print(e.get('path') or e.get('repo') or '')
        sys.exit(0)
sys.exit(1)
" "$list_json" "$name")"; then
        echo "Marketplace '$name' already registered (was: ${existing_source:-unknown source}); removing stale entry so it re-points at $source"
        claude plugin marketplace remove "$name"

        if ! claude plugin marketplace add "$source"; then
            echo "Error: re-adding marketplace '$name' at $source failed after removing the stale entry." >&2
            if [ -n "$existing_source" ]; then
                echo "Attempting to restore the previous registration at $existing_source ..." >&2
                if claude plugin marketplace add "$existing_source"; then
                    echo "Restored previous registration; '$name' still points at $existing_source (NOT $source)." >&2
                else
                    echo "Error: restore attempt also failed; marketplace '$name' is now UNREGISTERED." >&2
                fi
            fi
            return 1
        fi
    else
        claude plugin marketplace add "$source"
    fi
}

# is_real_run: decides whether this invocation should take the real
# three-arm measurement path or the free stub path. Extracted as its own
# testable function (issue #291).
#
# History: this used to gate solely on `[ "$EVENT_NAME" != "release" ]`, so a
# `release` event was the ONLY reachable trigger for the real path -- and
# that event can never fire in this repo, because releases are published by
# `release.yml` using `GITHUB_TOKEN`, and GitHub does not raise workflow
# events from `GITHUB_TOKEN` actions (documented anti-recursion safeguard).
# The real path was therefore permanently unreachable via `release`.
#
# Issue #304 then keyed the paid path off the TAG PUSH ITSELF (the same
# trigger release.yml uses), demoting the real/patch tag SHAPE (see
# is_release_tag()/is_patch_tag() below) to the deciding axis: a 3-component
# tag paid, a 4-component (MICRO) tag stayed free.
#
# Issue #309: under CalVer, tag SHAPE is not something the operator chooses --
# it's decided by whether today's date is already taken as a tag. On
# 2026-09-15, both v26.9.15 and v26.9.15.1 already existed, so ANY release
# cut that day was forced to be 4-component (v26.9.15.2) and therefore forced
# onto the free path, with no way to opt in short of waiting for the calendar
# or fabricating a date (see #307, closed unmerged, opened purely to obtain a
# paid run). Billing must not hang on a fact the operator can't control.
#
# Fix: the billing axis becomes an explicit opt-in declaration that names the
# EXACT tag it authorizes (read_optin_tag()/optin_authorizes() below) --
# read from OPTIN_FILE, e.g. docs/marc/benchmarks/OPTIN, a tracked,
# reviewable, single-line file. Tag shape is demoted from "the decision" to a
# VETO: is_patch_tag() can still force the free path (preserving #304's
# safety property -- a patch can never spend money, opt-in or not), but tag
# shape can no longer CAUSE spend by itself. A stale opt-in (left over from a
# prior release, naming a tag that already shipped) cannot cause spend
# either -- the next tag simply won't match it, so the declaration
# self-expires by design. This is deliberately a per-tag STRING, not a
# boolean flag, for exactly that reason.
#
# A `workflow_dispatch` run with its `real_run` input explicitly set to
# "true" is ALSO a real run, unchanged -- an explicit human opt-in that
# doesn't need the tag machinery at all. Every other case (push to a branch,
# pull_request, a patch-tag push, a release-shaped tag with no/mismatched
# opt-in, a workflow_dispatch left at its default `real_run=false`) stays on
# the free stub path -- see scripts/test_benchmark_dispatch_reachable.sh,
# which pins the full matrix.
is_release_tag() {
    # Exactly 3 numeric dot-separated components: vYY.M.D. A real
    # minor/major release per this repo's CalVer scheme. Takes an optional
    # ref argument (defaulting to $CURRENT_REF) so resolve_prev_release_tag()
    # below can classify an arbitrary ancestor tag, not just the current one.
    local ref="${1:-$CURRENT_REF}"
    [[ "$ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

is_patch_tag() {
    # A 4th MICRO component: vYY.M.D.MICRO. Disambiguates a second release
    # on an already-taken date -- a patch, never billed (issue #304). Same
    # optional-ref shape as is_release_tag() above; called from
    # resolve_prev_release_tag() to log which ancestor tags it skips.
    local ref="${1:-$CURRENT_REF}"
    [[ "$ref" =~ ^v[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]
}

# resolve_prev_release_tag: find the nearest ANCESTOR tag that is
# release-shaped (is_release_tag), skipping any patch tags in between.
#
# Issue #304 review (`@rev`): the original fix routed patch tags to the free
# stub path, which is correct in isolation, but left `PREV_TAG` resolution
# (main(), below) as a bare `git describe --tags --abbrev=0 "$CURRENT_REF^"`
# -- nearest tag by ANCESTRY, with no regard for release-vs-patch shape.
# Because a patch tag never reaches the real path, it never writes
# docs/marc/benchmarks/<tag>/manifest.json (issue #295's cache). So the next
# minor/major release's cache lookup would key on that patch tag, miss
# unconditionally, and re-run arm A -- ~15 extra PAID `claude` invocations,
# reopening exactly the waste this issue exists to close. Verified live
# ahead of this fix: `git describe --tags --abbrev=0 HEAD` resolved to
# `v26.9.15.1` (a patch tag) with no manifest under docs/marc/benchmarks/.
#
# Fix: walk back past any patch-shaped ancestor tag until a release-shaped
# one is found (or none exists).
#
# No-previous-release case: if no release-shaped ancestor tag exists at all,
# this returns an empty string -- the SAME signal `main()`'s existing
# `[ -z "$PREV_TAG" ]` check already treats as "no previous tag found,
# cannot perform A/B test" (a hard, honest failure, not a silent
# no-op/false-baseline comparison). That behavior is unchanged by this fix;
# only what counts as a valid PREV_TAG changed.
#
# Legacy tags: `git tag --sort=-creatordate` shows 3-component `v0.28.0`,
# `v0.27.0`, etc. alongside the CalVer `vYY.M.D` tags, and both classify as
# "release" under is_release_tag()'s rule (exactly 3 numeric components).
# This walk is deliberately allowed to cross that scheme boundary: v0.28.0
# is a genuine prior release of this project, not a different kind of
# artifact, and the only tag preceding the first CalVer release (v26.9.8).
# Refusing to walk that far back would just turn a legitimate (if old)
# comparison into the same hard "no previous tag" failure above, for no
# safety benefit -- there is nothing about the versioning-scheme change
# itself that makes v0.28.0 an invalid baseline.
resolve_prev_release_tag() {
    local from_ref="$1"
    local candidate="$from_ref"
    local skipped_any=false

    while true; do
        if ! candidate=$(git describe --tags --abbrev=0 "${candidate}^" 2>/dev/null); then
            if [ "$skipped_any" = true ]; then
                echo "No release-shaped ancestor tag found (walked back past one or more patch tags)." >&2
            fi
            echo ""
            return 0
        fi
        if is_release_tag "$candidate"; then
            echo "$candidate"
            return 0
        fi
        if is_patch_tag "$candidate"; then
            echo "Skipping patch tag $candidate while resolving the previous RELEASE tag (issue #304)." >&2
        fi
        skipped_any=true
    done
}

# OPTIN_FILE: path to the opt-in declaration (issue #309). Overridable via
# the environment so the companion test can point it at a temp fixture
# without touching the real repo file. Not exported anywhere in
# token-benchmark.yml -- it always resolves to the tracked path below.
OPTIN_FILE="${OPTIN_FILE:-$GITHUB_WORKSPACE/docs/marc/benchmarks/OPTIN}"

# read_optin_tag: read $file (default $OPTIN_FILE), trimmed of surrounding
# whitespace (including a trailing newline -- `git`/most editors add one on
# save, and that must not turn a correct opt-in into a non-match). Returns
# non-zero (with no output) if the file doesn't exist -- absence is the
# normal, safe state (no opt-in declared), not an error.
#
# Deliberately just a trim, never a regex/glob match against the tag: the
# comparison this feeds (optin_authorizes(), below) must be anchored exact-
# string equality, so a file containing "v26.9.1" (a prefix of "v26.9.16")
# or "v26.9.16-rc" (a superstring) does NOT authorize "v26.9.16" -- #309's
# AC. Trimming is about tolerating incidental whitespace, not about being
# lenient on the tag text itself.
read_optin_tag() {
    local file="${1:-$OPTIN_FILE}"
    [ -f "$file" ] || return 1
    local content
    content="$(cat "$file")" || return 1
    # Command substitution above already stripped any trailing newline(s).
    # Trim remaining leading/trailing whitespace (spaces, tabs, stray CR)
    # with a pure-bash idiom so this has no dependency on GNU-vs-BSD sed/tr
    # flag differences.
    content="${content#"${content%%[![:space:]]*}"}"
    content="${content%"${content##*[![:space:]]}"}"
    printf '%s' "$content"
}

# optin_authorizes: does the opt-in declaration at $file (default
# $OPTIN_FILE) name EXACTLY $ref (default $CURRENT_REF)? Anchored, full-
# string comparison -- no prefix/substring match. A missing file, an empty
# file, or a file naming any other tag (including a stale prior release's
# tag, or a prefix/superstring of $ref) all return non-zero, i.e. "does not
# authorize" -- the safe default in every case.
# shellcheck disable=SC2120 # called with explicit args by the companion test
optin_authorizes() {
    local ref="${1:-$CURRENT_REF}"
    local file="${2:-$OPTIN_FILE}"
    local declared
    declared="$(read_optin_tag "$file")" || return 1
    [ -n "$declared" ] || return 1
    [ "$declared" = "$ref" ]
}

is_real_run() {
    if [ "$EVENT_NAME" = "push" ] && [ "$REF_TYPE" = "tag" ]; then
        # is_patch_tag() is a HARD VETO (issue #304's safety property,
        # preserved by #309): a patch-shaped tag never pays, opt-in or not.
        if is_patch_tag; then
            return 1
        fi
        # Tag shape no longer CAUSES spend -- only an opt-in declaration
        # naming this exact tag does (issue #309).
        if optin_authorizes; then
            return 0
        fi
        return 1
    fi
    if [ "$EVENT_NAME" = "workflow_dispatch" ] && [ "$REAL_RUN_INPUT" = "true" ]; then
        return 0
    fi
    return 1
}

# wait_for_telemetry_file: bounded poll for a file to appear, instead of a
# single existence check right after `claude -p` returns.
#
# Issue #295: a paid run silently lost 3/15 `sweep` samples, all of them
# `claude` invocations that exited 0 (i.e. succeeded and were billed) but
# left no token-telemetry.jsonl behind. The suspected mechanism is a race
# between the CLI reporting the turn/process as finished and the Stop hook
# (a separate `bash`->`python3` subprocess the CLI itself spawns and whose
# internal scheduling/blocking semantics are not documented/observable from
# this repo) finishing its write to disk. We could NOT verify that
# mechanism directly -- it lives inside the closed-source `claude` binary,
# outside anything this repo controls or can instrument -- so this is a
# mitigation for the hypothesis, not a confirmed fix: a short bounded wait
# can only turn a would-be false "lost" into a correctly-counted "ok" (it
# never manufactures a sample that wasn't actually written), so it is safe
# to add even without proof of the exact root cause. If the file still
# never appears, the caller still gets a distinctly-labeled, honestly
# counted loss (see run_claude_safely below) instead of a silent drop.
wait_for_telemetry_file() {
    local file="$1"
    local max_attempts="${2:-5}"
    local delay_seconds="${3:-1}"
    local attempt
    for ((attempt = 1; attempt <= max_attempts; attempt++)); do
        [ -f "$file" ] && return 0
        sleep "$delay_seconds"
    done
    [ -f "$file" ]
}

# run_claude_safely: run `claude -p` $n times, collecting telemetry.
#
# Issue #295 fix: a run that exits 0 with no telemetry file (an instrument
# loss -- the invocation succeeded and was billed) is now reported with a
# DISTINCT message and counted separately from a run that exits non-zero (a
# genuine invocation failure). Before this fix both cases printed "failed
# with code $EXIT_CODE" -- including the misleading "failed with code 0" for
# a run that succeeded -- and were lumped into one silently-dropped bucket.
# A per-cell summary (ok/lost/failed vs the requested $n) is printed at the
# end of the loop so a short cell is visible in the raw log without having
# to reconstruct it from individual iteration lines.
run_claude_safely() {
    local task_prompt=$1
    local target_file=$2
    local state_dir=$3
    local n=$4
    local label=${5:-"$target_file"}
    local ok=0 lost=0 failed=0
    for ((i = 1; i <= n; i++)); do
        local temp_state="$state_dir/temp_run_$i"
        mkdir -p "$temp_state"
        export MARC_STATE_DIR="$temp_state"

        set +e
        claude --model "$MODEL" -p "$task_prompt"
        EXIT_CODE=$?
        set -e

        if [ $EXIT_CODE -ne 0 ]; then
            echo "Claude run $i ($label) FAILED: nonzero exit code $EXIT_CODE. Skipping telemetry (invocation error)."
            failed=$((failed + 1))
            continue
        fi

        if wait_for_telemetry_file "$temp_state/token-telemetry.jsonl" 5 1; then
            cat "$temp_state/token-telemetry.jsonl" >> "$target_file"
            ok=$((ok + 1))
        else
            echo "Claude run $i ($label) completed (exit 0, billed) but no telemetry file appeared after a bounded 5s wait. INSTRUMENT LOSS -- sample discarded, counted separately from an invocation failure."
            lost=$((lost + 1))
        fi
    done
    echo "  [summary] $label: ok=$ok lost=$lost(instrument) failed=$failed(invocation) -- requested n=$n"
}

# write_task_names: single source of truth for which task names exist in
# this run, consumed by scripts/benchmark_report.py so the report never
# drifts out of sync with the task set defined above (real run or stub).
write_task_names() {
    printf '%s\n' "${TASK_NAMES[@]}" > "$GITHUB_WORKSPACE/task_names.txt"
}

# write_iterations: issue #295, AC2/AC3. Written ONLY on the real
# measurement path (never the free stub path) so scripts/benchmark_report.py
# can compare each cell's actual sample count against the ITERATIONS this
# run actually requested and flag/fail on a shortfall. The stub path
# deliberately always emits n=2 fake samples per file regardless of
# $ITERATIONS -- those are illustrative fixtures for exercising the
# report/badge plumbing for free on every push/PR, not a real measurement,
# so they must never trip the shortfall/untrustworthy gate.
write_iterations() {
    printf '%s\n' "$ITERATIONS" > "$GITHUB_WORKSPACE/iterations.txt"
}

# emit_real_run_output: publish is_real_run()'s decision as a step output
# (issue #304) so token-benchmark.yml's publish steps ("Generate Dashboard
# Files" / "Commit Dashboard and Benchmarks") gate on the SAME computation as
# everything else in this script, instead of re-deriving "is this a real
# run" a second time in GitHub Actions expression syntax (issue #296 review
# previously found two independently-written expressions of the same fact
# could drift). Extracted as its own function so it's directly testable
# (see scripts/test_benchmark_dispatch_reachable.sh) without invoking main().
# $GITHUB_OUTPUT is unset when this is exercised outside an Actions runner,
# so this is a safe no-op there.
emit_real_run_output() {
    if [ -z "${GITHUB_OUTPUT:-}" ]; then
        return 0
    fi
    if is_real_run; then
        echo "real_run=true" >> "$GITHUB_OUTPUT"
    else
        echo "real_run=false" >> "$GITHUB_OUTPUT"
    fi
}

main() {
    emit_real_run_output

    if ! is_real_run; then
        echo "Not a real run (event=$EVENT_NAME, real_run input=$REAL_RUN_INPUT). Generating stubs for PR/push/unconfirmed dispatch."
        write_task_names
        for name in "${TASK_NAMES[@]}"; do
            cat << JSON > "baseline-$name.jsonl"
{"session_id": "sess-baseline-$name-1", "weighted": 15000, "turns": 5, "model": "stub-model", "timestamp": 1700000000}
{"session_id": "sess-baseline-$name-2", "weighted": 8000, "turns": 3, "model": "stub-model", "timestamp": 1700000100}
JSON
            cat << JSON > "post-$name.jsonl"
{"session_id": "sess-post-$name-1", "weighted": 12000, "turns": 4, "model": "stub-model", "timestamp": 1700000200}
{"session_id": "sess-post-$name-2", "weighted": 7500, "turns": 3, "model": "stub-model", "timestamp": 1700000300}
JSON
            cp "baseline-$name.jsonl" "toggle_baseline-$name.jsonl"
            cp "post-$name.jsonl" "toggle_post-$name.jsonl"
        done
        exit 0
    fi

    git fetch --tags --force
    PREV_TAG=$(resolve_prev_release_tag "$CURRENT_REF")

    if [ -z "$PREV_TAG" ]; then
        echo "Error: No previous tag found. Cannot perform A/B test."
        exit 1
    fi

    echo "Comparing $PREV_TAG (A) vs $CURRENT_REF (B/C), ${#TASK_NAMES[@]} tasks x $ITERATIONS iterations"

    # Install the CLI here, AFTER the (cheap) previous-tag resolution above but
    # BEFORE anything that hashes or otherwise depends on its version. This is
    # a deliberate reordering fix for issue #281 (item 3): the old script
    # captured `claude --version` at the top of the file, before this install
    # step ever ran, so on a clean runner it always hashed the constant
    # "unknown" and the drift guard never fired.
    npm i -g @anthropic-ai/claude-code

    CLAUDE_VERSION=$(resolve_claude_version) || exit 1
    TASK_HASH=$(compute_task_hash "$CLAUDE_VERSION" "$MODEL" "$(task_set_blob)" "$ITERATIONS")

    MANIFEST_PATH="docs/marc/benchmarks/$PREV_TAG/manifest.json"
    RERUN_A=true

    if [ -f "$MANIFEST_PATH" ]; then
        CACHED_HASH=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1])).get('task_hash', ''))" "$MANIFEST_PATH")
        ALL_BASELINES_PRESENT=true
        for name in "${TASK_NAMES[@]}"; do
            [ -f "docs/marc/benchmarks/$PREV_TAG/baseline-$name.jsonl" ] || ALL_BASELINES_PRESENT=false
        done
        if [ "$CACHED_HASH" == "$TASK_HASH" ] && [ "$ALL_BASELINES_PRESENT" = true ]; then
            echo "Manifest matches (task set + CLI + model + iterations unchanged)! Reusing baseline for all tasks."
            for name in "${TASK_NAMES[@]}"; do
                cp "docs/marc/benchmarks/$PREV_TAG/baseline-$name.jsonl" "baseline-$name.jsonl"
            done
            RERUN_A=false
        else
            echo "Manifest drift detected (task set, CLI, model, or iteration count changed) or a per-task baseline is missing. Re-running arm A."
        fi
    else
        echo "No cached baseline found for $PREV_TAG. Running arm A."
    fi

    if [ "$RERUN_A" = true ]; then
        echo "--- RUNNING ARM A ($PREV_TAG) ---"
        git worktree add ../arm-a "$PREV_TAG" || true
        pushd ../arm-a

        ensure_marketplace_added "./"
        claude plugin install marc@nexaduo

        mkdir -p .agents
        echo "[telemetry]" > .agents/team.toml
        echo "enabled = true" >> .agents/team.toml

        for idx in "${!TASK_NAMES[@]}"; do
            name="${TASK_NAMES[$idx]}"
            prompt="${TASK_PROMPTS[$idx]}"
            echo "  arm A / task=$name"
            rm -f "$GITHUB_WORKSPACE/baseline-$name.jsonl"
            run_claude_safely "$prompt" "$GITHUB_WORKSPACE/baseline-$name.jsonl" "$HOME/.claude/marc-state-a-$name" "$ITERATIONS" "arm A / task=$name"
        done

        popd
    fi

    echo "--- RUNNING ARM B ($CURRENT_REF with guard=350) ---"
    ensure_marketplace_added "./"
    claude plugin install marc@nexaduo

    mkdir -p .agents
    cat << CONFIG > .agents/team.toml
[telemetry]
enabled = true
[token_guard]
max_read_lines = 350
CONFIG

    for idx in "${!TASK_NAMES[@]}"; do
        name="${TASK_NAMES[$idx]}"
        prompt="${TASK_PROMPTS[$idx]}"
        echo "  arm B / task=$name"
        rm -f "$PWD/post-$name.jsonl"
        run_claude_safely "$prompt" "$PWD/post-$name.jsonl" "$HOME/.claude/marc-state-b-$name" "$ITERATIONS" "arm B / task=$name"
    done

    echo "--- RUNNING ARM C ($CURRENT_REF with guard=999999) ---"
    cat << CONFIG > .agents/team.toml
[telemetry]
enabled = true
[token_guard]
max_read_lines = 999999
CONFIG

    for idx in "${!TASK_NAMES[@]}"; do
        name="${TASK_NAMES[$idx]}"
        prompt="${TASK_PROMPTS[$idx]}"
        echo "  arm C / task=$name"
        rm -f "$PWD/toggle_baseline-$name.jsonl"
        run_claude_safely "$prompt" "$PWD/toggle_baseline-$name.jsonl" "$HOME/.claude/marc-state-c-$name" "$ITERATIONS" "arm C / task=$name"
        cp "$PWD/post-$name.jsonl" "$PWD/toggle_post-$name.jsonl"
    done

    write_task_names
    write_iterations

    # Save current run as baseline for future. On a `workflow_dispatch` real
    # run CURRENT_REF is a branch name (e.g. "main"), not a tag, so this
    # writes to a "main"-named cache directory instead of a version-named
    # one. Issue #296: a `workflow_dispatch` run with `real_run=true` DOES
    # now commit/push docs/ (see token-benchmark.yml's "Generate Dashboard
    # Files" / "Commit Dashboard and Benchmarks" `if:` conditions, which
    # publish on `release` OR a real-run dispatch) -- this directory is no
    # longer runner-only in that case, and a repeated real dispatch on the
    # same branch will overwrite its own prior "$CURRENT_REF" cache entry
    # (see docs/marc/benchmarks/README.md for why that's an intentional
    # cache, not an archive -- use a `run-<workflow-run-id>/` copy instead
    # to preserve a specific measurement permanently). It only stays
    # runner-only, captured solely by the artifact upload step, on the free
    # stub path (push/pull_request/dispatch-without-real_run).
    mkdir -p "docs/marc/benchmarks/$CURRENT_REF"
    for name in "${TASK_NAMES[@]}"; do
        cp "post-$name.jsonl" "docs/marc/benchmarks/$CURRENT_REF/baseline-$name.jsonl"
    done
    TASKS_JSON=$(python3 -c "
import json, sys
names = sys.argv[1].split(chr(31))
prompts = sys.argv[2].split(chr(31))
print(json.dumps([{'name': n, 'prompt': p} for n, p in zip(names, prompts)]))
" "$(IFS=$'\x1f'; echo "${TASK_NAMES[*]}")" "$(IFS=$'\x1f'; echo "${TASK_PROMPTS[*]}")")
    cat << JSON > "docs/marc/benchmarks/$CURRENT_REF/manifest.json"
{
  "model": "$MODEL",
  "tasks": $TASKS_JSON,
  "iterations": $ITERATIONS,
  "task_hash": "$TASK_HASH",
  "tag": "$CURRENT_REF",
  "claude_version": "$CLAUDE_VERSION"
}
JSON
}

# Only run main() when executed directly, not when sourced by the companion
# test (scripts/test_run_token_benchmark.sh), which needs the functions above
# defined without the release pipeline actually running.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
    main "$@"
fi
