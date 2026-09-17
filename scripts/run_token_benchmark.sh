#!/bin/bash
set -eo pipefail

EVENT_NAME="${GITHUB_EVENT_NAME:-push}"
CURRENT_REF="${GITHUB_REF_NAME:-main}"
GITHUB_WORKSPACE="${GITHUB_WORKSPACE:-$PWD}"
# Set by the workflow from the `real_run` workflow_dispatch input (issue
# #291). A dispatched run defaults to the stub path unless explicitly asked
# for the real (paid, multi-`claude`-invocation) measurement — see
# is_real_run() below.
REAL_RUN_INPUT="${REAL_RUN_INPUT:-false}"

# LOCAL_RUN (issue #314): opt-in flag for running this script BY HAND on a
# developer's own machine instead of a disposable CI runner (see docs/marc/
# benchmarks/README.md). The script was originally written assuming a
# throwaway runner -- `npm i -g` upgrading the global CLI, marketplace add/
# remove mutating the real Claude Code config, and a `../arm-a` worktree
# sibling to the checkout are all fine there and all unsafe on a machine the
# operator is actively using. LOCAL_RUN=true gates every one of those steps
# onto a safe equivalent; every other code path (including the free stub
# path and the CI real-run path) is completely unchanged by this flag.
LOCAL_RUN="${LOCAL_RUN:-false}"

# LOCAL_RUN_CONFIG_DIR (field report, first real local run): a fresh mktemp'd
# CLAUDE_CONFIG_DIR isolates plugin/marketplace state but also isolates
# authentication, so the CLI starts logged out and the billed preflight
# invocation fails. Set this to a directory you created and logged into
# yourself (`CLAUDE_CONFIG_DIR=<dir> claude` then `/login`) to reuse it as
# CLAUDE_CONFIG_DIR instead of a throwaway one -- see
# setup_local_run_isolation() below for the full rationale, including why
# `.credentials.json` redirection is forbidden instead. Unset by default:
# behaviour is then exactly the pre-existing fresh-mktemp isolation.
LOCAL_RUN_CONFIG_DIR="${LOCAL_RUN_CONFIG_DIR:-}"

# ARM_A_DIR: where the arm-A worktree is checked out. Defaults to the
# historical CI location; LOCAL_RUN mode overrides this to a disposable
# scratch directory in setup_local_run_isolation() below (issue #314 item 3).
ARM_A_DIR="${ARM_A_DIR:-../arm-a}"

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
#   bulk_read_forced - Added for issue #320 (the bulk-reader execution
#              layer). `sweep` showed nothing most plausibly because the
#              agent reached for `grep`, which the guard's `cat|less|more|
#              head|tail` regex never matches, so the guard likely never
#              fired in either arm on that task. This task asks for a
#              holistic understanding of core/scripts/dispatch_agent.py (483
#              lines, over the 350-line threshold) that a `grep` pattern
#              cannot substitute for (routing priority order, fallback
#              behavior, the full CLI contract) -- verified locally (see PR
#              body) to force an untargeted `Read` of the whole file, which
#              deterministically trips read-guard.sh. Issue #324: this is the
#              ONLY task arm D (below) is run against, specifically so it can
#              be compared against arm B on this task -- the meaningful
#              contrast for the study's claim is arm B (enforcement only, the
#              guard denies) vs arm D (enforcement + execution, the
#              bulk-reader worker summarizes instead), NOT arm D against the
#              guard-off control (arm C). Comparing D against C would be the
#              same enforcement-on-vs-enforcement-off comparison that already
#              produced the known-negative result (run 34987534132: guard
#              costs 2.6x, noise floor 33.7%) -- it would tell you nothing
#              about whether DELEGATING instead of DENYING helps.
TASK_NAMES=(control sweep neutral bulk_read_forced)
TASK_PROMPTS=(
    "read core/scripts/board.py and output a summary"
    "List every top-level (module-level) function definition across all non-test .py files in core/scripts/ (skip any file whose name starts with test_). Format each as '<filename>: <function_name>(...)'. Do not include methods defined inside classes, or functions nested inside other functions."
    "read AGENTS.md and summarize its \"Operating principles\" section in 3 bullet points"
    "Read core/scripts/dispatch_agent.py in full and explain, in prose, how it decides which harness to route a subagent to: the exact priority order between the --harness flag, team.toml routes, and the default hybrid matrix, and what happens when the chosen CLI binary is not on PATH."
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

# ARM_D_TASK (issue #324): the single task name arm D (bulk_reader=true) is
# measured against. Arm D makes real, billed `claude` invocations through
# the worker delegation path (#320/#322), so it deliberately does NOT repeat
# every task -- only the one task (bulk_read_forced, see its comment above)
# that is verified to deterministically trip the guard's enforcement path in
# the first place. Kept as a named constant, not a hardcoded string in
# main(), so scripts/benchmark_report.py's matching constant (documented
# there) and this script can never silently drift onto different task names.
ARM_D_TASK="bulk_read_forced"

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
# Issue #304 tried keying the paid path off a release-shaped TAG PUSH
# itself, on the theory that a tag push is raised by a human/CI identity
# (not GITHUB_TOKEN) and so, unlike `release`, actually fires. That worked,
# but issue #309 found the cost of that convenience: CI would spend real
# API credit on ANY tag push shaped like vYY.M.D, with no human in the loop
# at the moment of spend -- a scheduled/scripted `git push --tags`, a CI
# identity re-pushing tags, or simple operator error all bill automatically.
# The owner decided the paid measurement should never be CI's own decision:
# it now runs locally, by hand, on the owner's own account, at release time
# (see docs/marc/benchmarks/README.md for the exact local invocation).
#
# So the tag-shape axis is REMOVED entirely (not left as a second,
# lower-priority path -- the exact "money trap" #302/#309 flagged: any
# reachable automatic path is a real path, no matter how it's gated). The
# ONLY way to take the real (paid) path now is an explicit
# `workflow_dispatch` with its `real_run` input set to exactly "true" -- a
# deliberate, one-off human action, never an automatic consequence of
# pushing a tag, a branch, or opening a PR. Every other case (push to a
# branch, pull_request, ANY tag push regardless of shape, a
# workflow_dispatch left at its default `real_run=false` or any other
# value) stays on the free stub path -- fail CLOSED on anything ambiguous.
# See scripts/test_benchmark_dispatch_reachable.sh, which pins the full
# matrix.
#
# is_release_tag()/is_patch_tag() below are no longer consulted by
# is_real_run() (billing is no longer shape-dependent), but both stay in
# place: resolve_prev_release_tag() still calls both, purely to write an
# accurate stderr diagnostic naming WHY an ancestor tag without a manifest
# was skipped (patch tag vs. release-shaped-but-unmeasured vs. neither) --
# see its own comment. CHANGELOG.md's versioning note -- a 3-component
# CalVer tag (vYY.M.D) is a release, a 4-component tag (vYY.M.D.MICRO) is a
# patch of one -- is unchanged.
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
# RELEASE-SHAPED (is_release_tag), skipping patch tags in between. This
# answers a question about TAG SHAPE, not about measurement data: it is the
# previous release, full stop -- the thing arm A actually checks out
# (`git worktree add ../arm-a "$PREV_TAG"`) and the thing the run is labelled
# against ("Comparing $PREV_TAG (A) vs $CURRENT_REF (B/C)"). It has nothing
# to say about whether a cached baseline manifest happens to exist for that
# tag -- see resolve_cached_baseline_tag() below for that, separate,
# question.
#
# Issue #313 (bug introduced by #312): #312 correctly changed the CACHE
# question ("is there already a manifest we can reuse") to key off manifest
# presence instead of tag shape, but routed both the cache question and this
# comparison-target question through this one function/variable. On a repo
# where NO tag anywhere has a manifest yet (this repo's actual state as of
# #313 -- the paid path became workflow_dispatch-opt-in-only in #309, so
# most release-shaped tags are simply never measured), this function then
# returned an empty string and main()'s `[ -z "$PREV_TAG" ]` check treated
# "nothing is cached yet" as "no previous release exists", hard-failing the
# whole benchmark with `exit 1` even though a perfectly good previous
# release tag (just unmeasured) was sitting right there. Fix: this function
# goes back to being purely shape-based (its pre-#312 behavior); a separate
# resolve_cached_baseline_tag() now owns the manifest-presence CHECK (a
# direct check at the comparison target only -- see its own comment; the
# #312 ancestor WALK it originally introduced was removed by the #313 BLOCK
# fix for being unsafe, not merely for living in the wrong function).
#
# No-previous-release case: if no release-shaped ancestor tag exists at all,
# this returns an empty string -- the ONLY thing that should make main()'s
# `[ -z "$PREV_TAG" ]` check fire "no previous tag found, cannot perform A/B
# test". A missing CACHE is never fatal (see resolve_cached_baseline_tag).
resolve_prev_release_tag() {
    local from_ref="$1"
    local candidate="$from_ref"

    while true; do
        if ! candidate=$(git describe --tags --abbrev=0 "${candidate}^" 2>/dev/null); then
            echo ""
            return 0
        fi
        if is_patch_tag "$candidate"; then
            echo "Skipping patch tag $candidate (issue #304) while resolving the previous RELEASE-SHAPED tag." >&2
            continue
        fi
        if is_release_tag "$candidate"; then
            echo "$candidate"
            return 0
        fi
        echo "Skipping $candidate: not release-shaped while resolving the previous release tag." >&2
    done
}

# resolve_cached_baseline_tag: DIRECT existence check for a cached baseline
# manifest at the COMPARISON TARGET tag itself -- docs/marc/benchmarks/
# <prev_tag>/manifest.json. Takes resolve_prev_release_tag()'s OUTPUT as its
# argument (not $CURRENT_REF) -- it has nothing left to walk.
#
# Issue #313 BLOCK (`@rev` review of PR #313, verified by execution): this
# function used to walk PAST the comparison target and reuse an OLDER
# ancestor tag's cached baseline (issue #309/#312's original design). That is
# unsafe, not an optimization: arm A does not just replay stored numbers, it
# measures THIS REPO'S OWN PLUGIN CODE at the checked-out tag
# (`ensure_marketplace_added "./"` + `claude plugin install marc@nexaduo`
# inside the `../arm-a` worktree). main() only runs
# `git worktree add ../arm-a "$PREV_TAG"` on a cache MISS -- on a cache HIT
# it never checks out $PREV_TAG at all, so an older ancestor's reused
# baseline would silently stand in for a measurement of $PREV_TAG's own
# code, while the run still prints "Comparing $PREV_TAG (A) vs $CURRENT_REF"
# and the manifest written at the end records nothing about which tag the
# reused baseline actually came from. compute_task_hash() (above) hashes
# only claude_version:model:tasks_blob:iterations -- NOTHING about the
# repo's tree or commit -- so a plugin-code change between the older tag and
# $PREV_TAG leaves the hash unchanged and the stale, wrong-code baseline
# would be silently accepted. That produces plausible-looking wrong numbers,
# worse than the crash issue #313 set out to fix.
#
# A cached baseline is therefore reusable ONLY when it corresponds to the
# comparison target itself. If $PREV_TAG (the argument) has no manifest,
# that is an honest cache MISS -- arm A runs fresh against $PREV_TAG (the
# pre-#312 behavior issue #313 correctly restored), never a "search further
# back" that would substitute a different tag's repo code.
resolve_cached_baseline_tag() {
    local prev_tag="$1"
    if [ -f "docs/marc/benchmarks/$prev_tag/manifest.json" ]; then
        echo "$prev_tag"
        return 0
    fi
    echo "No cached baseline manifest found at docs/marc/benchmarks/$prev_tag/manifest.json. An older ancestor tag's baseline is never substituted (issue #313 BLOCK fix -- reusing it would measure DIFFERENT repo code than \$PREV_TAG). Running arm A fresh." >&2
    echo ""
    return 0
}

# setup_local_run_isolation: LOCAL_RUN=true's isolation step (issue #314,
# items 2 and 3). Verified empirically (issue #314): `claude plugin
# marketplace list --json` returns the host's real registrations
# (`['claude-plugins-official', 'nexaduo']`) by default, and an EMPTY list
# under a throwaway CLAUDE_CONFIG_DIR -- so pointing CLAUDE_CONFIG_DIR at a
# scratch directory keeps every marketplace/plugin mutation this script
# makes (ensure_marketplace_added, `claude plugin install marc@nexaduo`) off
# the developer's real Claude Code config. The arm-A worktree also moves off
# the repo's own parent directory (../arm-a) into the same scratch root, so
# a local run never creates a sibling directory next to the checkout either.
#
# The isolated config dir is intentionally NOT auto-deleted on exit -- the
# operator may want to inspect it (e.g. to confirm what actually got
# installed) -- so its path is printed instead. The arm-A git worktree
# REGISTRATION is cleaned up via a trap scoped to exactly $ARM_A_DIR, so a
# local run never leaves the main checkout's `git worktree list` pointing at
# a now-orphaned scratch path, and never touches anything outside it.
#
# LOCAL_RUN_CONFIG_DIR (field report, first real local run): a fresh
# mktemp'd CLAUDE_CONFIG_DIR isolates plugin/marketplace state correctly but
# also isolates AUTHENTICATION, so the CLI starts logged out and the
# preflight invocation fails every time. The fix is NOT to redirect
# `.credentials.json` into the scratch dir -- that was tried and it is
# DANGEROUS: the CLI refreshes its OAuth token via write-temp-then-rename,
# which replaces a symlink (or diverges from a copy) with a fresh regular
# file inside the scratch dir; deleting that scratch dir afterwards then
# destroys the only copy of the refreshed token and LOGS THE OPERATOR OUT of
# their real host session. This happened. Never read, print, copy, move,
# symlink, or delete anything named `.credentials.json` anywhere in this
# script.
#
# Instead, when LOCAL_RUN_CONFIG_DIR is set, the operator supplies their OWN
# persistent config directory that they have already logged into once
# (`CLAUDE_CONFIG_DIR=<dir> claude` then `/login`), and CLAUDE_CONFIG_DIR
# points there instead of under a throwaway mktemp root. That directory is
# never created here -- an auto-created, never-logged-into typo'd path would
# silently reproduce the exact logged-out preflight failure this exists to
# fix, after already spending the one billed preflight invocation -- so a
# missing or non-directory path is a hard abort. It is also never deleted;
# it is the operator's own persistent asset, reused across runs.
setup_local_run_isolation() {
    LOCAL_RUN_SCRATCH_DIR="$(mktemp -d "${TMPDIR:-/tmp}/marc-local-run.XXXXXX")"
    if [ -n "${LOCAL_RUN_CONFIG_DIR:-}" ]; then
        if [ ! -e "$LOCAL_RUN_CONFIG_DIR" ]; then
            echo "Error: LOCAL_RUN_CONFIG_DIR is set to '$LOCAL_RUN_CONFIG_DIR' but that path does not exist. Create it and log into it first: mkdir -p '$LOCAL_RUN_CONFIG_DIR' && CLAUDE_CONFIG_DIR='$LOCAL_RUN_CONFIG_DIR' claude, then run /login. This script will never auto-create it -- a typo'd path would silently start the preflight invocation logged out again." >&2
            exit 1
        fi
        if [ ! -d "$LOCAL_RUN_CONFIG_DIR" ]; then
            echo "Error: LOCAL_RUN_CONFIG_DIR is set to '$LOCAL_RUN_CONFIG_DIR' but that path is not a directory. Point it at a directory you created and logged into with CLAUDE_CONFIG_DIR='$LOCAL_RUN_CONFIG_DIR' claude followed by /login." >&2
            exit 1
        fi
        CLAUDE_CONFIG_DIR="$LOCAL_RUN_CONFIG_DIR"
    else
        CLAUDE_CONFIG_DIR="$LOCAL_RUN_SCRATCH_DIR/claude-config"
        mkdir -p "$CLAUDE_CONFIG_DIR"
    fi
    export CLAUDE_CONFIG_DIR
    ARM_A_DIR="$LOCAL_RUN_SCRATCH_DIR/arm-a"
    # PREFLIGHT_STATE_DIR: PR #316 (`@rev` BLOCK). This used to be the fixed,
    # non-randomized `$HOME/.claude/marc-state-preflight`, never cleaned
    # between invocations -- unlike everything else LOCAL_RUN touches, which
    # lives under this same fresh $LOCAL_RUN_SCRATCH_DIR. A leftover row from
    # any earlier local run at that fixed path let run_local_preflight()
    # report success by echoing back stale data, exactly when the isolation
    # risk it exists to catch had materialized. Putting it inside the
    # mktemp'd scratch root makes it empty by construction on every run --
    # it cannot inherit anything, because nothing else has ever written here.
    PREFLIGHT_STATE_DIR="$LOCAL_RUN_SCRATCH_DIR/preflight-state"
    mkdir -p "$PREFLIGHT_STATE_DIR"
    echo "Local run mode (LOCAL_RUN=true): isolating this run at $LOCAL_RUN_SCRATCH_DIR" >&2
    if [ -n "${LOCAL_RUN_CONFIG_DIR:-}" ]; then
        echo "  config dir mode: REUSED operator-owned config (LOCAL_RUN_CONFIG_DIR set)" >&2
        echo "  CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR (this is YOUR persistent, already-logged-in config dir -- it is not deleted or modified by this script beyond the plugin/marketplace mutations it always makes)" >&2
    else
        echo "  config dir mode: FRESH throwaway config (LOCAL_RUN_CONFIG_DIR unset)" >&2
        echo "  CLAUDE_CONFIG_DIR=$CLAUDE_CONFIG_DIR (never the host's real Claude Code config -- inspect or delete this directory yourself when done)" >&2
    fi
    echo "  arm-A worktree will be checked out at $ARM_A_DIR (not ../arm-a)" >&2
    echo "  preflight telemetry state dir: $PREFLIGHT_STATE_DIR (always under the fresh per-run scratch root, never inside a reused config dir, and never \$HOME/.claude -- see PR #316)" >&2
    trap 'git worktree remove --force "$ARM_A_DIR" 2>/dev/null || true; git worktree prune 2>/dev/null || true' EXIT
}

# add_arm_a_worktree: `git worktree add` for arm A, with a local-mode-aware
# safety difference. In CI (LOCAL_RUN=false, the historical default) the
# blind `|| true` is left exactly as-is -- out of scope for issue #314,
# which is about local runs, and CI's runner is disposable so a leftover
# worktree from a previous invocation essentially never happens there.
#
# In LOCAL_RUN=true mode a developer machine is NOT disposable, so a real
# `git worktree add` failure (bad tag, permissions, disk) must still abort
# loudly instead of being swallowed by `|| true` -- the very next line
# (`pushd "$dir"`) would otherwise succeed against whatever happened to be
# there and silently measure arm A against the wrong code.
#
# PR #316 (`@rev` review): this used to also detect and clear a
# "pre-existing worktree at $dir" before adding. That branch was dead code:
# $dir is ARM_A_DIR, which under LOCAL_RUN=true is set exactly once, inside
# setup_local_run_isolation(), to `$LOCAL_RUN_SCRATCH_DIR/arm-a` where
# $LOCAL_RUN_SCRATCH_DIR is a brand-new `mktemp -d` directory this same
# invocation just created -- nothing can already exist at that path, so
# `[ -d "$dir" ]` could never be true. `@sec` had only cleared the branch as
# "safe" because it could currently target nothing but the scratch dir;
# removed rather than left in place, so a future change that lets $dir alias
# something real doesn't inherit an untested destructive path.
add_arm_a_worktree() {
    local dir="$1" tag="$2"
    if [ "$LOCAL_RUN" = "true" ]; then
        git worktree add "$dir" "$tag"
    else
        git worktree add "$dir" "$tag" || true
    fi
}

# run_local_preflight: item 4 of issue #314, the HIGHEST-VALUE part of local
# mode. Measurements are written by the plugin's Stop hook via
# $MARC_STATE_DIR (see core/scripts/token_telemetry.py's state_dir()) --
# run_claude_safely already sets that env var explicitly per invocation, so
# the WRITE LOCATION itself is unaffected by CLAUDE_CONFIG_DIR isolation.
# What is genuinely uncertain under isolation is whether the Stop hook fires
# AT ALL: it only runs if the CLI resolves CLAUDE_PLUGIN_ROOT to the plugin
# installed via `claude plugin install marc@nexaduo` -- and under
# CLAUDE_CONFIG_DIR isolation that install lives in a throwaway config root
# the CLI has never used before. Rather than assume that resolves correctly,
# this makes exactly ONE real, billed `claude -p` invocation and asserts a
# telemetry row was actually written before committing to the ~44 remaining
# paid invocations of a full run (run 35046956691 burned ~19 billed
# invocations for the related reason of spending before verifying).
run_local_preflight() {
    local state_dir="$1"
    local target_file="$2"
    # PR #316 (`@rev` BLOCK, reproduced without any real `claude`
    # invocation): a plain `[ -s "$target_file" ]` check after the run is not
    # enough -- it can pass on a STALE row left by an earlier invocation that
    # wrote to this exact per-run file, even if THIS invocation's Stop hook
    # never fired. Item 1 of the fix (setup_local_run_isolation, above) makes
    # $state_dir fresh-by-construction so that can no longer happen via the
    # normal call path; this is item 2, belt-and-suspenders: capture the row
    # count for the one file this single invocation would write to
    # (temp_run_1, since n=1 below) BEFORE calling out, and require it to
    # have gone up afterwards, rather than trusting non-emptiness alone.
    local telemetry_file="$state_dir/temp_run_1/token-telemetry.jsonl"
    local rows_before=0
    if [ -f "$telemetry_file" ]; then
        rows_before=$(wc -l < "$telemetry_file" | tr -d ' ')
    fi

    echo "--- LOCAL PREFLIGHT: one real (billed) invocation to confirm telemetry survives CLAUDE_CONFIG_DIR isolation before spending on the rest of the run ---"
    rm -f "$target_file"
    run_claude_safely "Reply with just the word OK." "$target_file" "$state_dir" 1 "local-preflight"

    local rows_after=0
    if [ -f "$telemetry_file" ]; then
        rows_after=$(wc -l < "$telemetry_file" | tr -d ' ')
    fi

    if [ -s "$target_file" ] && [ "$rows_after" -gt "$rows_before" ]; then
        echo "Local preflight OK: telemetry recorded ($((rows_after - rows_before)) new row(s), strictly more than the $rows_before present beforehand) at $state_dir. Proceeding with the full measurement."
        return 0
    fi

    if [ "${LAST_RUN_FAILED:-0}" -gt 0 ]; then
        echo "Error: LOCAL PREFLIGHT FAILED. The one real (billed) invocation itself failed (nonzero exit) -- see the 'FAILED: nonzero exit code' line above. This is an invocation/CLI error, not evidence about whether telemetry survives CLAUDE_CONFIG_DIR isolation. ABORTING before further spend." >&2
    else
        echo "Error: LOCAL PREFLIGHT FAILED. The one real (billed) invocation completed but no NEW telemetry row was written under CLAUDE_CONFIG_DIR isolation (CLAUDE_CONFIG_DIR=${CLAUDE_CONFIG_DIR:-unset}; rows before=$rows_before, after=$rows_after at $telemetry_file). The invocation succeeded but nothing was recorded -- the Stop hook did not fire under config isolation. This means the plugin's Stop hook telemetry does not survive config isolation on this machine -- proceeding would spend on the ~44 remaining paid invocations for ZERO samples, the exact failure mode issue #314 exists to prevent. ABORTING before further spend." >&2
    fi
    return 1
}

# install_claude_cli_if_needed: issue #314 item 1. LOCAL_RUN=true skips the
# global `npm i -g @anthropic-ai/claude-code` install entirely -- on a
# developer machine that command upgrades the CLI the operator's own active
# sessions may be running, possibly mid-session. The CI path (LOCAL_RUN
# unset/false, the historical default) is completely unchanged: it still
# installs before resolve_claude_version() is called, preserving the issue
# #281 item 3 ordering fix (version capture must happen AFTER install so it
# never falls back to hashing a placeholder).
install_claude_cli_if_needed() {
    if [ "$LOCAL_RUN" = "true" ]; then
        echo "Local run mode: skipping 'npm i -g @anthropic-ai/claude-code' (would upgrade the host's global CLI, possibly mid-session). Using whatever 'claude' is already on PATH."
        return 0
    fi
    npm i -g @anthropic-ai/claude-code
}

# resolve_task_index: pure helper, no side effects. Prints the index of
# `target` within TASK_NAMES (so a caller can look up its matching
# TASK_PROMPTS entry) or returns non-zero if `target` is not in the task
# set. Extracted so arm D's "find my task's prompt" logic is directly
# testable without a real measurement environment (issue #324).
resolve_task_index() {
    local target="$1"
    local idx
    for idx in "${!TASK_NAMES[@]}"; do
        if [ "${TASK_NAMES[$idx]}" = "$target" ]; then
            echo "$idx"
            return 0
        fi
    done
    return 1
}

# arm_d_team_toml: pure function, no side effects. Returns the
# `.agents/team.toml` body for arm D (issue #324): the SAME guard threshold
# as arm B (max_read_lines = 350, so the guard still fires the same way it
# does in arm B) PLUS `bulk_reader = true`, so an over-threshold untargeted
# Read that arm B would simply deny instead gets delegated to the
# bulk-reader worker (#320/#322) -- this is what actually exercises the
# execution layer, not merely the enforcement layer arm B/C already cover.
# Extracted as its own function (rather than inlined via heredoc the way
# arm B/C are) specifically so scripts/test_run_token_benchmark.sh can
# assert `bulk_reader = true` is actually present without needing a real
# measurement environment -- the literal gap issue #324 exists to close.
arm_d_team_toml() {
    cat << 'CONFIG'
[telemetry]
enabled = true
[token_guard]
max_read_lines = 350
bulk_reader = true
CONFIG
}

is_real_run() {
    # Issue #309: the ONLY reachable path to the paid measurement. Fail
    # CLOSED on anything else -- no tag shape, no branch, no event other
    # than an explicit workflow_dispatch, ever implies "true" here.
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
    # Exposed as a global (deliberately NOT `local`) so a caller that needs to
    # distinguish "the invocation itself failed" from "it succeeded but
    # nothing was recorded" -- run_local_preflight(), PR #316 -- can read it
    # without this function's return code carrying extra meaning for its
    # other, unrelated callers.
    LAST_RUN_FAILED=$failed
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

    if [ "$LOCAL_RUN" = "true" ]; then
        setup_local_run_isolation
    fi

    git fetch --tags --force
    PREV_TAG=$(resolve_prev_release_tag "$CURRENT_REF")

    if [ -z "$PREV_TAG" ]; then
        echo "Error: No previous release tag found. Cannot perform A/B test."
        exit 1
    fi

    CACHE_TAG=$(resolve_cached_baseline_tag "$PREV_TAG")

    echo "Comparing $PREV_TAG (A) vs $CURRENT_REF (B/C), ${#TASK_NAMES[@]} tasks x $ITERATIONS iterations"

    # Install the CLI here, AFTER the (cheap) previous-tag resolution above but
    # BEFORE anything that hashes or otherwise depends on its version. This is
    # a deliberate reordering fix for issue #281 (item 3): the old script
    # captured `claude --version` at the top of the file, before this install
    # step ever ran, so on a clean runner it always hashed the constant
    # "unknown" and the drift guard never fired.
    install_claude_cli_if_needed

    CLAUDE_VERSION=$(resolve_claude_version) || exit 1
    TASK_HASH=$(compute_task_hash "$CLAUDE_VERSION" "$MODEL" "$(task_set_blob)" "$ITERATIONS")

    if [ "$LOCAL_RUN" = "true" ]; then
        ensure_marketplace_added "./"
        claude plugin install marc@nexaduo
        mkdir -p .agents
        printf '[telemetry]\nenabled = true\n' > .agents/team.toml
        if ! run_local_preflight "$PREFLIGHT_STATE_DIR" "$GITHUB_WORKSPACE/preflight.jsonl"; then
            exit 1
        fi
    fi

    RERUN_A=true

    if [ -n "$CACHE_TAG" ]; then
        MANIFEST_PATH="docs/marc/benchmarks/$CACHE_TAG/manifest.json"
        CACHED_HASH=$(python3 -c "import json, sys; print(json.load(open(sys.argv[1])).get('task_hash', ''))" "$MANIFEST_PATH")
        ALL_BASELINES_PRESENT=true
        for name in "${TASK_NAMES[@]}"; do
            [ -f "docs/marc/benchmarks/$CACHE_TAG/baseline-$name.jsonl" ] || ALL_BASELINES_PRESENT=false
        done
        if [ "$CACHED_HASH" == "$TASK_HASH" ] && [ "$ALL_BASELINES_PRESENT" = true ]; then
            echo "Manifest matches (task set + CLI + model + iterations unchanged)! Reusing baseline from $CACHE_TAG for all tasks."
            for name in "${TASK_NAMES[@]}"; do
                cp "docs/marc/benchmarks/$CACHE_TAG/baseline-$name.jsonl" "baseline-$name.jsonl"
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
        add_arm_a_worktree "$ARM_A_DIR" "$PREV_TAG"
        pushd "$ARM_A_DIR"

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

    # Issue #324: arm D actually exercises the bulk-reader execution layer
    # #322 shipped -- until this arm existed, NO benchmark arm ever set
    # `bulk_reader = true`, so the feature it measures could never be
    # measured (arm B/C above only ever toggle max_read_lines). Runs against
    # ARM_D_TASK ONLY (not the full task set) -- these are real, billed
    # `claude` invocations through the worker delegation path, and only
    # ARM_D_TASK is verified to deterministically trip the guard's
    # enforcement path in the first place (see its comment in the task-set
    # block above), so running the other tasks here would just spend money
    # measuring a branch that structurally never fires.
    #
    # THE COMPARISON THIS ARM EXISTS FOR: arm B (task=$ARM_D_TASK,
    # enforcement only -- the guard denies) vs arm D (task=$ARM_D_TASK,
    # enforcement + execution -- the worker summarizes instead). NOT arm D
    # against arm C (the guard-off control): that comparison would just
    # repeat the enforcement-on-vs-enforcement-off comparison that already
    # produced the known-negative result (run 34987534132: guard costs 2.6x,
    # noise floor 33.7%) and would say nothing about whether delegating
    # instead of denying actually helps. scripts/benchmark_report.py prints
    # this exact B-vs-D contrast explicitly, by name, for the same reason.
    #
    # THIS ARM DOES NOT FEED THE BADGE. Per #303/#305 the badge is fed the
    # SHIPPED-DEFAULT arm, and `bulk_reader` defaults to OFF (see
    # docs/team.toml.example) -- arm D is opt-in-on-opt-in and nobody ships
    # it by default. token-benchmark.yml's "Generate Dashboard Files" step
    # is unchanged by this arm and continues reading only
    # baseline-control.jsonl / toggle_baseline-control.jsonl / arm B+C
    # neutral files -- see the comment there.
    echo "--- RUNNING ARM D ($CURRENT_REF with guard=350 + bulk_reader=true, task=$ARM_D_TASK only) ---"
    arm_d_team_toml > .agents/team.toml

    if ! ARM_D_IDX=$(resolve_task_index "$ARM_D_TASK"); then
        echo "Error: ARM_D_TASK='$ARM_D_TASK' is not in TASK_NAMES. Arm D cannot run -- refusing to silently skip it (issue #324's whole point is that this arm must not go unmeasured)." >&2
        exit 1
    fi
    arm_d_prompt="${TASK_PROMPTS[$ARM_D_IDX]}"
    echo "  arm D / task=$ARM_D_TASK"
    rm -f "$PWD/bulk_reader-$ARM_D_TASK.jsonl"
    run_claude_safely "$arm_d_prompt" "$PWD/bulk_reader-$ARM_D_TASK.jsonl" "$HOME/.claude/marc-state-d-$ARM_D_TASK" "$ITERATIONS" "arm D / task=$ARM_D_TASK"

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
