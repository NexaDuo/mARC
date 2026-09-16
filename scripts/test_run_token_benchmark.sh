#!/bin/bash
# Regression test for issue #281.
#
# The release A/B pipeline in run_token_benchmark.sh cannot be exercised
# end-to-end without a real git tag, the claude CLI, and credentials (see the
# PR description). Instead this test sources the script — which, thanks to
# the `[[ "${BASH_SOURCE[0]}" == "${0}" ]]` guard at the bottom, defines its
# functions WITHOUT running main() — and exercises the extracted, pure/
# stubbable pieces directly:
#
#   1. ensure_marketplace_added(): the marketplace-registration idempotency
#      fix. Asserts it (a) does NOT abort when the marketplace is already
#      registered, (b) removes the stale entry before re-adding so the name
#      is re-pointed at the new source, (c) does NOT call remove when nothing
#      is registered yet, and (d) still propagates a genuine `add` failure
#      (not masked by a blind `|| true`).
#
#   2. resolve_claude_version() / compute_task_hash(): proves the ordering
#      bug this issue diagnosed, and the fix. Before the fix, the version was
#      captured before CLI install, via `claude --version 2>/dev/null ||
#      echo unknown` — on a clean runner (no `claude` on PATH yet) this is
#      always the constant "unknown", regardless of which CLI version is
#      about to be installed, so the hash's version component never changes.
#      This test reproduces that exact fragment against a PATH with no
#      `claude` at all (today's "before install" moment) with two different
#      target versions, and shows both produce "unknown" -> identical hash
#      (today's inertia). It then shows resolve_claude_version()/
#      compute_task_hash() against a PATH with a real (stubbed) `claude`
#      binary for those same two versions produces two DIFFERENT hashes
#      (today's fix), and that resolve_claude_version() fails loudly (does
#      not print "unknown") when `claude --version` fails post-install.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET="$SCRIPT_DIR/run_token_benchmark.sh"

FAILS=0
pass() { echo "ok   - $1"; }
fail() { echo "FAIL - $1"; FAILS=$((FAILS + 1)); }

# Source the script. GITHUB_EVENT_NAME is irrelevant here: main() never runs
# because we are sourcing, not executing.
# shellcheck source=/dev/null
source "$TARGET"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

# -----------------------------------------------------------------------
# Part 1: ensure_marketplace_added()
# -----------------------------------------------------------------------

mkdir -p "$WORK/source-a/.claude-plugin"
cat > "$WORK/source-a/.claude-plugin/marketplace.json" << 'JSON'
{"name": "nexaduo"}
JSON

FAKE_BIN="$WORK/fakebin"
mkdir -p "$FAKE_BIN"
CALL_LOG="$WORK/calls.log"
: > "$CALL_LOG"

# Fake `claude` driven by env vars so each sub-test can control behavior
# without editing the stub between runs.
cat > "$FAKE_BIN/claude" << 'STUB'
#!/bin/bash
echo "$*" >> "$MARC_TEST_CALL_LOG"
if [ "$1" = "plugin" ] && [ "$2" = "marketplace" ]; then
    case "$3" in
        list)
            # Simulates the failure `@rev` found in the PR #283 review: a
            # `list --json` call that exits non-zero (empty registry is the
            # documented trigger case, plausible on arm A's very first call).
            if [ "${MARC_TEST_LIST_FAILS:-0}" = "1" ]; then
                echo "list: no marketplaces configured" >&2
                exit 1
            fi
            if [ "${MARC_TEST_REGISTERED:-0}" = "1" ]; then
                echo '[{"name": "nexaduo", "path": "/old/path"}]'
            else
                echo '[]'
            fi
            exit 0
            ;;
        remove)
            echo "removed" >> "$MARC_TEST_CALL_LOG"
            exit 0
            ;;
        add)
            if [ "${MARC_TEST_ADD_FAILS:-0}" = "1" ]; then
                echo "network error" >&2
                exit 1
            fi
            exit 0
            ;;
    esac
fi
exit 0
STUB
chmod +x "$FAKE_BIN/claude"

export PATH="$FAKE_BIN:$PATH"
export MARC_TEST_CALL_LOG="$CALL_LOG"

# 1a. Already registered -> must NOT abort, must remove then add.
export MARC_TEST_REGISTERED=1
export MARC_TEST_ADD_FAILS=0
: > "$CALL_LOG"
if ensure_marketplace_added "$WORK/source-a"; then
    pass "ensure_marketplace_added does not abort when already registered"
else
    fail "ensure_marketplace_added aborted on already-registered marketplace"
fi
if grep -q "marketplace remove nexaduo" "$CALL_LOG"; then
    pass "ensure_marketplace_added removes the stale entry before re-adding"
else
    fail "ensure_marketplace_added did not call 'marketplace remove' for a stale entry"
fi
if grep -q "marketplace add $WORK/source-a" "$CALL_LOG"; then
    pass "ensure_marketplace_added re-adds after removing"
else
    fail "ensure_marketplace_added did not re-add after removing"
fi

# 1b. Not registered yet -> must NOT call remove.
export MARC_TEST_REGISTERED=0
: > "$CALL_LOG"
ensure_marketplace_added "$WORK/source-a" > /dev/null
if grep -q "marketplace remove" "$CALL_LOG"; then
    fail "ensure_marketplace_added called 'remove' when nothing was registered"
else
    pass "ensure_marketplace_added skips remove when nothing is registered"
fi

# 1c. A genuine add failure (e.g. network) must still propagate, not be
# swallowed like a blind `|| true` would.
export MARC_TEST_REGISTERED=0
export MARC_TEST_ADD_FAILS=1
if ensure_marketplace_added "$WORK/source-a" > /dev/null 2>&1; then
    fail "ensure_marketplace_added swallowed a genuine 'add' failure"
else
    pass "ensure_marketplace_added propagates a genuine 'add' failure (not masked)"
fi
export MARC_TEST_ADD_FAILS=0

# 1d. `@rev` finding (PR #283 review, HIGH): 'list --json' exiting non-zero
# (empty registry is the documented trigger, plausible on arm A's very first
# call, before anything has ever been registered) must be treated as an
# ordinary "not registered yet" outcome under `set -eo pipefail` — NOT let
# the pipeline's own failure abort the whole function/script before `add` is
# ever reached. This test is run in a SEPARATE subshell driven by `bash -c`
# with `set -eo pipefail` explicitly re-enabled (matching the real script's
# ambient state), so a regression here reproduces the exact "DRIVE EXIT: 1,
# SURVIVED never printed" failure `@rev` found, not just a soft assertion
# inside this already-relaxed (`set -euo pipefail` without `-e` propagating
# through `if`) test driver.
export MARC_TEST_REGISTERED=0
export MARC_TEST_LIST_FAILS=1
: > "$CALL_LOG"
if PATH="$FAKE_BIN:$PATH" MARC_TEST_CALL_LOG="$CALL_LOG" \
    MARC_TEST_LIST_FAILS=1 MARC_TEST_REGISTERED=0 MARC_TEST_ADD_FAILS=0 \
    bash -c '
        set -eo pipefail
        source "'"$TARGET"'"
        ensure_marketplace_added "'"$WORK"'/source-a"
        echo SURVIVED
    ' > "$WORK/1d.out" 2>&1
then
    if grep -q "^SURVIVED$" "$WORK/1d.out"; then
        pass "ensure_marketplace_added survives a failing 'list --json' (does not abort under set -eo pipefail)"
    else
        fail "ensure_marketplace_added exited 0 but did not reach past the list-failure path (unexpected)"
    fi
else
    fail "ensure_marketplace_added aborted the whole script when 'list --json' failed (see: $(cat "$WORK/1d.out"))"
fi
if grep -q "marketplace add $WORK/source-a" "$CALL_LOG"; then
    pass "ensure_marketplace_added still calls 'add' after a failing 'list --json'"
else
    fail "ensure_marketplace_added did not call 'add' after a failing 'list --json'"
fi
if grep -q "marketplace remove" "$CALL_LOG"; then
    fail "ensure_marketplace_added called 'remove' after a failing (i.e. 'nothing registered') 'list --json'"
else
    pass "ensure_marketplace_added skips remove when 'list --json' failed (treated as not-registered)"
fi
unset MARC_TEST_LIST_FAILS

# 1e. An empty registry (`list --json` succeeds, returns `[]`) must also
# result in a plain add, no remove — same outcome as 1b, restated explicitly
# per the coordinator's ask to cover "list returning an empty/absent
# registry" as its own case, independent of the failing-list case above.
export MARC_TEST_REGISTERED=0
: > "$CALL_LOG"
if ensure_marketplace_added "$WORK/source-a" > /dev/null; then
    pass "ensure_marketplace_added survives an empty ('[]') registry"
else
    fail "ensure_marketplace_added aborted on an empty ('[]') registry"
fi
if grep -q "marketplace remove" "$CALL_LOG"; then
    fail "ensure_marketplace_added called 'remove' on an empty registry"
else
    pass "ensure_marketplace_added skips remove on an empty registry"
fi
unset MARC_TEST_REGISTERED MARC_TEST_ADD_FAILS

# -----------------------------------------------------------------------
# Part 2: the CLI-drift-guard ordering bug (issue #281 item 3)
# -----------------------------------------------------------------------

EMPTY_BIN="$WORK/emptybin"
mkdir -p "$EMPTY_BIN"

# 2a. Reproduce TODAY's bug: capture "before install" (no `claude` on PATH),
# for two DIFFERENT target CLI versions that haven't been installed yet. The
# OLD script's exact fragment: `claude --version 2>/dev/null || echo unknown`.
old_capture() {
    PATH="$EMPTY_BIN" claude --version 2>/dev/null || echo "unknown"
}
before_v1="$(old_capture)"
before_v2="$(old_capture)"
if [ "$before_v1" = "unknown" ] && [ "$before_v1" = "$before_v2" ]; then
    pass "reproduced today's bug: pre-install capture is the constant 'unknown' regardless of target CLI version"
else
    fail "did not reproduce the pre-install 'unknown' constant (got '$before_v1' / '$before_v2')"
fi

# 2b. After the fix: capture happens with a real (stubbed) `claude` present,
# i.e. AFTER install. Two different installed versions must yield two
# different hashes.
CLAUDE_BIN="$WORK/claudebin"
mkdir -p "$CLAUDE_BIN"

write_claude_stub() {
    local version="$1"
    cat > "$CLAUDE_BIN/claude" << STUB
#!/bin/bash
if [ "\$1" = "--version" ]; then
    echo "$version (Claude Code)"
    exit 0
fi
exit 0
STUB
    chmod +x "$CLAUDE_BIN/claude"
}

write_claude_stub "2.1.200"
v1="$(PATH="$CLAUDE_BIN" resolve_claude_version)"
hash1="$(compute_task_hash "$v1" "claude-sonnet-5" "$(task_set_blob)" "$ITERATIONS")"

write_claude_stub "2.2.0"
v2="$(PATH="$CLAUDE_BIN" resolve_claude_version)"
hash2="$(compute_task_hash "$v2" "claude-sonnet-5" "$(task_set_blob)" "$ITERATIONS")"

if [ "$v1" != "$v2" ] && [ "$hash1" != "$hash2" ]; then
    pass "post-install capture differs across CLI versions ('$v1' vs '$v2') and so does the hash ('$hash1' vs '$hash2') -- drift guard is live"
else
    fail "post-install capture/hash did not differ across CLI versions (v1='$v1' v2='$v2' hash1='$hash1' hash2='$hash2')"
fi

# -----------------------------------------------------------------------
# Part 3: the task-SET hash covers the whole set, not just one task
# (issue #275) -- changing any task's name/prompt, adding/removing a task,
# or changing the iteration count must change the hash. This is the
# specific regression #275 calls out: the old compute_task_hash() took a
# single task string and would not notice drift in any task other than the
# (former) only one.
# -----------------------------------------------------------------------

base_blob="$(task_set_blob)"
base_hash="$(compute_task_hash "2.1.272 (Claude Code)" "claude-sonnet-5" "$base_blob" "$ITERATIONS")"

# 3a. Changing a task's prompt (simulating drift in any task, not just the
# first) changes the hash.
altered_blob="${base_blob/read AGENTS.md/read SOMETHING_ELSE.md}"
if [ "$altered_blob" = "$base_blob" ]; then
    fail "test setup bug: altered_blob did not actually differ from base_blob"
else
    altered_hash="$(compute_task_hash "2.1.272 (Claude Code)" "claude-sonnet-5" "$altered_blob" "$ITERATIONS")"
    if [ "$altered_hash" != "$base_hash" ]; then
        pass "task-set hash changes when a non-first task's prompt changes"
    else
        fail "task-set hash did NOT change when a non-first task's prompt changed -- drift guard blind to tasks other than the first (issue #275 regression)"
    fi
fi

# 3b. Changing the iteration count changes the hash.
iter_hash="$(compute_task_hash "2.1.272 (Claude Code)" "claude-sonnet-5" "$base_blob" "3")"
if [ "$iter_hash" != "$base_hash" ]; then
    pass "task-set hash changes when the iteration count changes"
else
    fail "task-set hash did NOT change when the iteration count changed"
fi

# 3c. Removing a task (shorter blob) changes the hash.
truncated_blob="${base_blob%%|neutral=*}|"
if [ "$truncated_blob" = "$base_blob" ]; then
    fail "test setup bug: truncated_blob did not actually differ from base_blob"
else
    truncated_hash="$(compute_task_hash "2.1.272 (Claude Code)" "claude-sonnet-5" "$truncated_blob" "$ITERATIONS")"
    if [ "$truncated_hash" != "$base_hash" ]; then
        pass "task-set hash changes when a task is removed from the set"
    else
        fail "task-set hash did NOT change when a task was removed from the set"
    fi
fi

# 2c. A `claude --version` failure AFTER install must fail loudly, not hash
# a placeholder.
if PATH="$EMPTY_BIN" resolve_claude_version > /dev/null 2>&1; then
    fail "resolve_claude_version did not fail when claude --version fails post-install"
else
    pass "resolve_claude_version fails loudly (non-zero) instead of hashing a placeholder"
fi

# -----------------------------------------------------------------------
# Part 4: resolve_prev_release_tag() -- the COMPARISON TARGET, pure tag
# shape (issue #304 `@rev` review, BLOCKING; issue #313 restored this after
# #312 conflated it with the cache lookup).
#
# PREV_TAG used to be `git describe --tags --abbrev=0 "$CURRENT_REF^"` --
# nearest tag by ANCESTRY, with no regard for release-vs-patch shape.
# Because a patch tag never takes the real (paid) path, it never writes
# docs/marc/benchmarks/<tag>/manifest.json, so the next minor/major
# release's cache lookup would key on that patch tag, miss unconditionally,
# and re-run arm A -- extra PAID `claude` invocations, reopening the exact
# waste issue #304 exists to close. Issue #304 fixed this by walking to the
# nearest RELEASE-SHAPED ancestor tag instead, skipping patch tags. This is
# a question about TAG SHAPE ONLY -- resolve_prev_release_tag() does NOT
# consult manifest presence at all; see Part 5 below for the separate
# manifest-presence CHECK (resolve_cached_baseline_tag()). This builds a
# small, real git repo with a fabricated tag history and exercises
# resolve_prev_release_tag() against it directly (a `cd`, not a mock -- the
# function shells out to real `git describe`).
# -----------------------------------------------------------------------

# Every `git tag` below is pinned with `-c tag.gpgsign=false`: a lightweight
# tag must behave identically regardless of the operator's global git
# config, and a `tag.gpgsign=true` global setting otherwise turns a plain
# `git tag <name>` into a failing signed-tag attempt ("fatal: no tag
# message?" -- reproduced while writing this test) with no GPG key
# configured in this sandbox. See AGENTS.md's "guard scripts against
# ambient config" lesson.
# A manifest's CONTENT is irrelevant to resolve_prev_release_tag() -- it
# only checks the file's existence (main() is the one that reads task_hash
# etc. out of it) -- so this helper writes a minimal placeholder.
write_manifest() {
    local repo="$1" tag="$2"
    mkdir -p "$repo/docs/marc/benchmarks/$tag"
    echo '{}' > "$repo/docs/marc/benchmarks/$tag/manifest.json"
}

GIT_REPO="$WORK/prev-tag-repo"
mkdir -p "$GIT_REPO"
(
    cd "$GIT_REPO"
    git init -q
    git config user.email "test@example.com"
    git config user.name "test"

    # commit 1: a legacy 3-component release tag (pre-CalVer scheme). HAS a
    # manifest -- it was actually measured.
    echo a > f.txt && git add f.txt && git commit -qm "c1"
    git -c tag.gpgsign=false tag v0.28.0

    # commit 2: a CalVer release tag. HAS a manifest.
    echo b > f.txt && git add f.txt && git commit -qm "c2"
    git -c tag.gpgsign=false tag v26.9.15

    # commit 3: a CalVer PATCH tag on top of it (same day, disambiguated).
    # Never has a manifest of its own by construction (issue #304).
    echo c > f.txt && git add f.txt && git commit -qm "c3"
    git -c tag.gpgsign=false tag v26.9.15.1

    # commit 4 (HEAD): the next minor/major release under test.
    echo d > f.txt && git add f.txt && git commit -qm "c4"
    git -c tag.gpgsign=false tag v26.9.16
)
write_manifest "$GIT_REPO" v0.28.0
write_manifest "$GIT_REPO" v26.9.15

# 4a. Walking back from a new release tag whose immediate ancestor is a
# PATCH tag (no manifest by construction) must skip it and land on the
# preceding tag THAT HAS A MANIFEST -- not the patch tag itself, and not
# silently miss (empty string) either.
got="$(cd "$GIT_REPO" && resolve_prev_release_tag "v26.9.16" 2>/dev/null)"
if [ "$got" = "v26.9.15" ]; then
    pass "resolve_prev_release_tag() skips an intervening patch tag (v26.9.15.1) and lands on the preceding MEASURED tag (v26.9.15)"
else
    fail "resolve_prev_release_tag() from v26.9.16 -- expected 'v26.9.15', got '$got'"
fi

# 4b. Same repo, but confirm the walk is willing to cross the legacy
# v0.x.y <-> vYY.M.D scheme boundary when the nearer tag is itself
# unreachable (i.e. walking from v26.9.15 directly, past its own manifest,
# to whatever's further back): a legitimate prior measurement, not a
# different kind of artifact, and there is no reason to hard-fail just
# because the versioning scheme changed at that point.
got_legacy="$(cd "$GIT_REPO" && resolve_prev_release_tag "v26.9.15" 2>/dev/null)"
if [ "$got_legacy" = "v0.28.0" ]; then
    pass "resolve_prev_release_tag() walks back across the legacy v0.x.y/CalVer scheme boundary to v0.28.0 when that is the nearest MEASURED ancestor"
else
    fail "resolve_prev_release_tag() from v26.9.15 -- expected 'v0.28.0' (legacy boundary), got '$got_legacy'"
fi

# 4c. No-previous-release case: a repo whose only ancestor tag is a PATCH
# tag, with nothing measured further back, must degrade to an empty
# PREV_TAG -- the SAME signal main()'s existing
# `[ -z "$PREV_TAG" ]` -> "Error: No previous tag found. Cannot perform
# A/B test." path already treats as a hard, honest failure. It must NOT
# silently return the patch tag itself (which has no manifest) or fabricate
# a comparison.
NO_PREV_REPO="$WORK/no-prev-repo"
mkdir -p "$NO_PREV_REPO"
(
    cd "$NO_PREV_REPO"
    git init -q
    git config user.email "test@example.com"
    git config user.name "test"
    echo a > f.txt && git add f.txt && git commit -qm "c1"
    git -c tag.gpgsign=false tag v26.9.15.1

    echo b > f.txt && git add f.txt && git commit -qm "c2"
    git -c tag.gpgsign=false tag v26.9.16
)
got_none="$(cd "$NO_PREV_REPO" && resolve_prev_release_tag "v26.9.16" 2>/dev/null)"
if [ -z "$got_none" ]; then
    pass "resolve_prev_release_tag() returns empty (honest 'no previous release' signal) when only a patch tag exists further back, not the patch tag itself"
else
    fail "resolve_prev_release_tag() with no release-shaped ancestor -- expected empty, got '$got_none'"
fi

# -----------------------------------------------------------------------
# 4d. resolve_prev_release_tag() is PURE SHAPE -- it must land on the
# nearest release-shaped ancestor tag regardless of whether that tag has a
# manifest. Sequence: release N (HAS a manifest) -> release N+1
# (release-shaped, NO manifest, because #309 made the paid path opt-in) ->
# release N+2 (current, under test). The COMPARISON TARGET (this function)
# must be N+1 -- it is the actual previous release, and is what gets
# checked out for arm A -- even though it has no cached data. See Part 5
# below for resolve_cached_baseline_tag(), which checks directly for N+1's
# own manifest and reports a MISS there (never substitutes N's).
# -----------------------------------------------------------------------
UNMEASURED_RELEASE_REPO="$WORK/unmeasured-release-repo"
mkdir -p "$UNMEASURED_RELEASE_REPO"
(
    cd "$UNMEASURED_RELEASE_REPO"
    git init -q
    git config user.email "test@example.com"
    git config user.name "test"

    # release N: measured, has a manifest.
    echo a > f.txt && git add f.txt && git commit -qm "c1"
    git -c tag.gpgsign=false tag v26.9.10

    # release N+1: release-shaped, but NEVER measured (no manifest) --
    # the #309 case.
    echo b > f.txt && git add f.txt && git commit -qm "c2"
    git -c tag.gpgsign=false tag v26.9.13

    # release N+2 (HEAD): the release under test.
    echo c > f.txt && git add f.txt && git commit -qm "c3"
    git -c tag.gpgsign=false tag v26.9.16
)
write_manifest "$UNMEASURED_RELEASE_REPO" v26.9.10
got_comparison_target="$(cd "$UNMEASURED_RELEASE_REPO" && resolve_prev_release_tag "v26.9.16" 2>/dev/null)"
if [ "$got_comparison_target" = "v26.9.13" ]; then
    pass "resolve_prev_release_tag() lands on the nearest release-shaped ancestor (v26.9.13) as the COMPARISON TARGET even though it has no manifest -- issue #313"
else
    fail "resolve_prev_release_tag() from v26.9.16 -- expected the release-shaped v26.9.13 as comparison target, got '$got_comparison_target'"
fi

# -----------------------------------------------------------------------
# Part 5: resolve_cached_baseline_tag() -- the CACHE SOURCE, a DIRECT check
# at the comparison target only, never an ancestor walk (issue #313 BLOCK,
# `@rev` review of PR #313, verified by execution).
#
# It now takes resolve_prev_release_tag()'s OUTPUT as its argument -- the
# comparison target itself -- not $CURRENT_REF, and has nothing left to
# walk. #309/#312's original ancestor walk (reuse an OLDER tag's baseline
# when the comparison target itself has none) is FORBIDDEN behavior now:
# arm A measures THIS REPO'S OWN PLUGIN CODE at the checked-out tag, and
# main() only checks out $PREV_TAG on a cache MISS -- a cache HIT from an
# older ancestor would silently substitute a different tag's repo code
# while the run still labels itself against $PREV_TAG. TASK_HASH does not
# cover repo code, so nothing would catch the drift.
# -----------------------------------------------------------------------

# 5a. Reuses UNMEASURED_RELEASE_REPO from 4d: release N (v26.9.10, HAS a
# manifest) -> release N+1 (v26.9.13, release-shaped, NO manifest) ->
# release N+2 (v26.9.16, current). resolve_prev_release_tag() resolves the
# comparison target to v26.9.13 (asserted in 4d above). Checking the cache
# AT v26.9.13 (not walking past it to v26.9.10) MUST be a MISS (empty),
# even though an older ancestor (v26.9.10) does have a manifest -- reusing
# it would measure v26.9.10's repo code for a v26.9.13 comparison.
got_cache_miss_at_target="$(cd "$UNMEASURED_RELEASE_REPO" && resolve_cached_baseline_tag "v26.9.13" 2>/dev/null)"
if [ -z "$got_cache_miss_at_target" ]; then
    pass "resolve_cached_baseline_tag() is a cache MISS when the comparison target (v26.9.13) has no manifest of its own, even though an older ancestor (v26.9.10) does -- issue #313 BLOCK fix"
else
    fail "resolve_cached_baseline_tag('v26.9.13') -- expected empty (cache miss, no ancestor substitution), got '$got_cache_miss_at_target'"
fi

# 5b. Same GIT_REPO as Part 4 (v0.28.0 manifest -> v26.9.15 manifest ->
# v26.9.15.1 patch -> v26.9.16 HEAD): resolve_prev_release_tag() resolves
# the comparison target to v26.9.15 (asserted in 4a above), which itself
# HAS a manifest -- checking the cache directly at that tag must be a HIT.
got_cache_hit_at_target="$(cd "$GIT_REPO" && resolve_cached_baseline_tag "v26.9.15" 2>/dev/null)"
if [ "$got_cache_hit_at_target" = "v26.9.15" ]; then
    pass "resolve_cached_baseline_tag() is a cache HIT when the comparison target itself has a manifest (v26.9.15)"
else
    fail "resolve_cached_baseline_tag('v26.9.15') -- expected 'v26.9.15', got '$got_cache_hit_at_target'"
fi

# -----------------------------------------------------------------------
# Part 6: THE BOOTSTRAP CASE (issue #313) -- the regression #312 missed.
# A repo where release-shaped ancestor tags EXIST, but NO tag anywhere
# (release-shaped or not) has ever written a manifest -- this repo's actual
# state as of #313, because the paid path became workflow_dispatch-only in
# #309 and nobody has run it since. resolve_prev_release_tag() (the
# comparison target) MUST still resolve non-empty here -- there IS a
# previous release, it's just unmeasured -- and main()'s
# `[ -z "$PREV_TAG" ]` fatal check must NOT fire. resolve_cached_baseline_tag()
# legitimately returns empty here (nothing to reuse), and that must NOT be
# treated as fatal either -- it is the ordinary "run arm A fresh" case.
# -----------------------------------------------------------------------
BOOTSTRAP_REPO="$WORK/bootstrap-repo"
mkdir -p "$BOOTSTRAP_REPO"
(
    cd "$BOOTSTRAP_REPO"
    git init -q
    git config user.email "test@example.com"
    git config user.name "test"

    # release N-1: release-shaped, but no manifest was ever written for it.
    echo a > f.txt && git add f.txt && git commit -qm "c1"
    git -c tag.gpgsign=false tag v26.9.10

    # release N: release-shaped, also no manifest.
    echo b > f.txt && git add f.txt && git commit -qm "c2"
    git -c tag.gpgsign=false tag v26.9.13

    # release N+1 (HEAD): the release under test. No docs/marc/benchmarks/
    # directory exists anywhere in this repo at all.
    echo c > f.txt && git add f.txt && git commit -qm "c3"
    git -c tag.gpgsign=false tag v26.9.16
)
bootstrap_comparison="$(cd "$BOOTSTRAP_REPO" && resolve_prev_release_tag "v26.9.16" 2>/dev/null)"
if [ "$bootstrap_comparison" = "v26.9.13" ]; then
    pass "BOOTSTRAP CASE: resolve_prev_release_tag() resolves a non-empty comparison target (v26.9.13) when no tag anywhere has a manifest -- issue #313"
else
    fail "BOOTSTRAP CASE: resolve_prev_release_tag() -- expected 'v26.9.13', got '$bootstrap_comparison' (empty here is exactly the #313 regression: benchmark unrunnable)"
fi

bootstrap_cache="$(cd "$BOOTSTRAP_REPO" && resolve_cached_baseline_tag "$bootstrap_comparison" 2>/dev/null)"
if [ -z "$bootstrap_cache" ]; then
    pass "BOOTSTRAP CASE: resolve_cached_baseline_tag() returns empty (no cached baseline exists) without being treated as fatal -- issue #313"
else
    fail "BOOTSTRAP CASE: resolve_cached_baseline_tag() -- expected empty (no manifest anywhere), got '$bootstrap_cache'"
fi

# The combination that actually mattered in production: a non-empty
# comparison target with an empty cache tag must NOT be fatal in main()'s
# logic -- verified structurally here (both resolvers agree with the
# no-cache-is-not-fatal contract); main()'s own `[ -z "$PREV_TAG" ]` early
# exit is exercised indirectly since PREV_TAG is populated above.
if [ -n "$bootstrap_comparison" ] && [ -z "$bootstrap_cache" ]; then
    pass "BOOTSTRAP CASE: comparison target resolved while cache stays empty -- main()'s fatal check (keyed on PREV_TAG only) would NOT fire"
else
    fail "BOOTSTRAP CASE: unexpected combination (comparison='$bootstrap_comparison' cache='$bootstrap_cache')"
fi

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
