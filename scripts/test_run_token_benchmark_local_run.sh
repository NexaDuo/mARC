#!/bin/bash
# Regression test for issue #314: LOCAL_RUN=true mode.
#
# Sources run_token_benchmark.sh (main() never executes, same guard as
# scripts/test_run_token_benchmark.sh) and exercises the new local-mode
# helpers directly with stubbed `claude`/`npm` state. No real invocation is
# ever made here: `claude` is always a fake shell script, never the real
# paid CLI, and REAL_RUN_INPUT is never set to "true".
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

FAKE_BIN="$WORK/fakebin"
mkdir -p "$FAKE_BIN"

# -----------------------------------------------------------------------
# Part 1: LOCAL_RUN mode must skip the global `npm i -g` install.
# -----------------------------------------------------------------------
NPM_CALL_LOG="$WORK/npm-calls.log"
: > "$NPM_CALL_LOG"
cat > "$FAKE_BIN/npm" << STUB
#!/bin/bash
echo "\$*" >> "$NPM_CALL_LOG"
exit 0
STUB
chmod +x "$FAKE_BIN/npm"

# Call the real production function, install_claude_cli_if_needed(), the
# one main() actually invokes -- not a reproduced fragment -- so a mutation
# to main()'s real behavior is caught here.
: > "$NPM_CALL_LOG"
LOCAL_RUN=true PATH="$FAKE_BIN:$PATH" install_claude_cli_if_needed > /dev/null
if [ -s "$NPM_CALL_LOG" ]; then
    fail "LOCAL_RUN=true still invoked npm (global install not skipped)"
else
    pass "LOCAL_RUN=true skips the global 'npm i -g' install"
fi

: > "$NPM_CALL_LOG"
LOCAL_RUN=false PATH="$FAKE_BIN:$PATH" install_claude_cli_if_needed > /dev/null
if grep -q "i -g @anthropic-ai/claude-code" "$NPM_CALL_LOG"; then
    pass "LOCAL_RUN=false (CI path, unchanged) still performs the global npm install"
else
    fail "LOCAL_RUN=false no longer performs the global npm install (CI regression)"
fi

# -----------------------------------------------------------------------
# Part 2: local mode must still resolve a REAL version from the local CLI
# and never hash a placeholder (issue #281 item 3 must not regress).
# -----------------------------------------------------------------------
CLAUDE_BIN="$WORK/claudebin"
mkdir -p "$CLAUDE_BIN"
cat > "$CLAUDE_BIN/claude" << 'STUB'
#!/bin/bash
if [ "$1" = "--version" ]; then
    echo "9.9.9-local (Claude Code)"
    exit 0
fi
exit 0
STUB
chmod +x "$CLAUDE_BIN/claude"

local_version="$(PATH="$CLAUDE_BIN" resolve_claude_version)"
if [ "$local_version" = "9.9.9-local (Claude Code)" ] && [ "$local_version" != "unknown" ]; then
    pass "local mode resolves the REAL local 'claude --version' (never a placeholder)"
else
    fail "local mode did not resolve the real local CLI version, got '$local_version'"
fi

local_hash="$(compute_task_hash "$local_version" "claude-sonnet-5" "$(task_set_blob)" "$ITERATIONS")"
placeholder_hash="$(compute_task_hash "unknown" "claude-sonnet-5" "$(task_set_blob)" "$ITERATIONS")"
if [ "$local_hash" != "$placeholder_hash" ]; then
    pass "local-mode version hash differs from the 'unknown' placeholder hash -- drift guard stays live"
else
    fail "local-mode version hash matched the 'unknown' placeholder hash -- drift guard would be silently disabled"
fi

EMPTY_BIN="$WORK/emptybin"
mkdir -p "$EMPTY_BIN"
if PATH="$EMPTY_BIN" resolve_claude_version > /dev/null 2>&1; then
    fail "local mode: resolve_claude_version did not fail when the real local CLI is missing/broken"
else
    pass "local mode: resolve_claude_version still exits non-zero when the local CLI can't be resolved"
fi

# -----------------------------------------------------------------------
# Part 3: setup_local_run_isolation() sets CLAUDE_CONFIG_DIR to a fresh,
# throwaway directory and repoints ARM_A_DIR away from ../arm-a; it must
# never touch CLAUDE_CONFIG_DIR when LOCAL_RUN is not exercised.
# -----------------------------------------------------------------------
(
    unset CLAUDE_CONFIG_DIR || true
    LOCAL_RUN=true
    export LOCAL_RUN
    setup_local_run_isolation
    if [ -n "${CLAUDE_CONFIG_DIR:-}" ] && [ -d "$CLAUDE_CONFIG_DIR" ]; then
        echo "PASS_CONFIG_DIR_SET"
    else
        echo "FAIL_CONFIG_DIR_NOT_SET"
    fi
    case "$CLAUDE_CONFIG_DIR" in
        "$HOME/.claude"|"$HOME/.claude/"*)
            echo "FAIL_CONFIG_DIR_IS_REAL_HOME_CONFIG"
            ;;
        *)
            echo "PASS_CONFIG_DIR_IS_SCRATCH"
            ;;
    esac
    case "$ARM_A_DIR" in
        ../arm-a)
            echo "FAIL_ARM_A_STILL_SIBLING"
            ;;
        *)
            echo "PASS_ARM_A_DISPOSABLE"
            ;;
    esac
) > "$WORK/part3.out" 2>/dev/null

if grep -q "^PASS_CONFIG_DIR_SET$" "$WORK/part3.out"; then
    pass "setup_local_run_isolation() sets CLAUDE_CONFIG_DIR to an existing directory"
else
    fail "setup_local_run_isolation() did not set CLAUDE_CONFIG_DIR to an existing directory"
fi
if grep -q "^PASS_CONFIG_DIR_IS_SCRATCH$" "$WORK/part3.out"; then
    pass "setup_local_run_isolation() points CLAUDE_CONFIG_DIR away from the host's real ~/.claude config"
else
    fail "setup_local_run_isolation() left CLAUDE_CONFIG_DIR pointed at the host's real ~/.claude config"
fi
if grep -q "^PASS_ARM_A_DISPOSABLE$" "$WORK/part3.out"; then
    pass "setup_local_run_isolation() repoints ARM_A_DIR away from ../arm-a"
else
    fail "setup_local_run_isolation() left ARM_A_DIR at ../arm-a (sibling-directory mutation not avoided)"
fi

# CI path (LOCAL_RUN not exercised at all): CLAUDE_CONFIG_DIR and ARM_A_DIR
# must stay exactly as the caller's environment set them -- no local-mode
# function is even called on this path in main(), but pin the default here
# too so a future refactor can't silently start calling it unconditionally.
(
    unset CLAUDE_CONFIG_DIR || true
    unset LOCAL_RUN || true
    # shellcheck source=/dev/null
    source "$TARGET"
    if [ -z "${CLAUDE_CONFIG_DIR:-}" ]; then
        echo "PASS_CONFIG_DIR_UNTOUCHED"
    else
        echo "FAIL_CONFIG_DIR_SET_UNEXPECTEDLY=$CLAUDE_CONFIG_DIR"
    fi
    if [ "$ARM_A_DIR" = "../arm-a" ]; then
        echo "PASS_ARM_A_DEFAULT"
    else
        echo "FAIL_ARM_A_DEFAULT_CHANGED=$ARM_A_DIR"
    fi
) > "$WORK/part3b.out" 2>/dev/null

if grep -q "^PASS_CONFIG_DIR_UNTOUCHED$" "$WORK/part3b.out"; then
    pass "non-local mode (LOCAL_RUN unset/false) never sets CLAUDE_CONFIG_DIR"
else
    fail "non-local mode set CLAUDE_CONFIG_DIR unexpectedly ($(cat "$WORK/part3b.out"))"
fi
if grep -q "^PASS_ARM_A_DEFAULT$" "$WORK/part3b.out"; then
    pass "non-local mode keeps ARM_A_DIR at the historical ../arm-a default"
else
    fail "non-local mode's ARM_A_DIR default changed ($(cat "$WORK/part3b.out"))"
fi

# -----------------------------------------------------------------------
# Part 4: run_local_preflight() aborts (non-zero) when no telemetry row is
# recorded, and succeeds when one is. Stubs `claude -p ...` to exit 0 (a
# "billed, succeeded" invocation) while controlling whether it also drops a
# token-telemetry.jsonl file in $MARC_STATE_DIR, the same file
# run_claude_safely() waits for.
# -----------------------------------------------------------------------
PREFLIGHT_BIN="$WORK/preflightbin"
mkdir -p "$PREFLIGHT_BIN"
cat > "$PREFLIGHT_BIN/claude" << 'STUB'
#!/bin/bash
if [ "$1" = "--model" ]; then
    if [ "${MARC_TEST_WRITE_TELEMETRY:-0}" = "1" ]; then
        echo '{"session_id": "sess-preflight-1", "weighted": 123, "turns": 1, "model": "stub-model", "timestamp": 1700000000}' > "$MARC_STATE_DIR/token-telemetry.jsonl"
    fi
    exit 0
fi
exit 0
STUB
chmod +x "$PREFLIGHT_BIN/claude"

# 4a. No telemetry row recorded -> preflight must fail (non-zero), loudly.
PREFLIGHT_STATE_MISS="$WORK/preflight-state-miss"
PREFLIGHT_TARGET_MISS="$WORK/preflight-miss.jsonl"
mkdir -p "$PREFLIGHT_STATE_MISS"
if PATH="$PREFLIGHT_BIN:$PATH" MARC_TEST_WRITE_TELEMETRY=0 \
    run_local_preflight "$PREFLIGHT_STATE_MISS" "$PREFLIGHT_TARGET_MISS" \
    > "$WORK/4a.out" 2>&1; then
    fail "run_local_preflight() exited 0 even though no telemetry row was recorded (should ABORT)"
else
    pass "run_local_preflight() aborts (non-zero) when no telemetry row is recorded"
fi
if grep -q "LOCAL PREFLIGHT FAILED" "$WORK/4a.out"; then
    pass "run_local_preflight() prints a clear failure message naming the telemetry-isolation risk"
else
    fail "run_local_preflight() did not print the expected 'LOCAL PREFLIGHT FAILED' message ($(cat "$WORK/4a.out"))"
fi

# 4b. A telemetry row IS recorded -> preflight must succeed.
PREFLIGHT_STATE_HIT="$WORK/preflight-state-hit"
PREFLIGHT_TARGET_HIT="$WORK/preflight-hit.jsonl"
mkdir -p "$PREFLIGHT_STATE_HIT"
if PATH="$PREFLIGHT_BIN:$PATH" MARC_TEST_WRITE_TELEMETRY=1 \
    run_local_preflight "$PREFLIGHT_STATE_HIT" "$PREFLIGHT_TARGET_HIT" \
    > "$WORK/4b.out" 2>&1; then
    pass "run_local_preflight() succeeds when a telemetry row is recorded"
else
    fail "run_local_preflight() aborted even though a telemetry row WAS recorded ($(cat "$WORK/4b.out"))"
fi
if [ -s "$PREFLIGHT_TARGET_HIT" ]; then
    pass "run_local_preflight() leaves the recorded telemetry row in the target file"
else
    fail "run_local_preflight() succeeded but the target file has no content"
fi

# -----------------------------------------------------------------------
# Part 4c (PR #316, `@rev` BLOCK): the exact reproduction. A STALE telemetry
# row already sits at the path the preflight inspects for THIS invocation
# (temp_run_1/token-telemetry.jsonl) -- e.g. left over from an earlier local
# run -- and the stub `claude` for this invocation writes NOTHING (simulating
# the Stop hook failing to fire under CLAUDE_CONFIG_DIR isolation). Before
# the fix, `[ -s "$target_file" ]` alone would pass because run_claude_safely
# copies the stale row into $target_file via `cat`. This MUST abort.
# -----------------------------------------------------------------------
PREFLIGHT_STATE_STALE="$WORK/preflight-state-stale"
PREFLIGHT_TARGET_STALE="$WORK/preflight-stale.jsonl"
mkdir -p "$PREFLIGHT_STATE_STALE/temp_run_1"
echo '{"session_id": "sess-STALE-leftover", "weighted": 999, "turns": 1, "model": "stub-model", "timestamp": 1600000000}' \
    > "$PREFLIGHT_STATE_STALE/temp_run_1/token-telemetry.jsonl"

if PATH="$PREFLIGHT_BIN:$PATH" MARC_TEST_WRITE_TELEMETRY=0 \
    run_local_preflight "$PREFLIGHT_STATE_STALE" "$PREFLIGHT_TARGET_STALE" \
    > "$WORK/4c.out" 2>&1; then
    fail "run_local_preflight() exited 0 with only a STALE pre-existing telemetry row and no new write (the exact @rev reproduction) -- should ABORT"
else
    pass "run_local_preflight() aborts when only a stale pre-existing row is present and nothing new was recorded"
fi
if grep -q "did not fire under config isolation" "$WORK/4c.out"; then
    pass "run_local_preflight() names the Stop-hook-did-not-fire case distinctly for the stale-row scenario"
else
    fail "run_local_preflight() did not distinguish the stale-row case ($(cat "$WORK/4c.out"))"
fi

# -----------------------------------------------------------------------
# Part 5 (field report): LOCAL_RUN_CONFIG_DIR lets the operator supply their
# own already-logged-in config dir instead of a throwaway one. It must be
# honored when it exists, hard-abort by name when it doesn't (or is a file),
# leave the default mktemp behaviour untouched when unset, and -- the
# regression that matters -- PREFLIGHT_STATE_DIR must stay under the fresh
# per-run scratch root, never inside the supplied (persistent, reused)
# config dir, in either mode.
# -----------------------------------------------------------------------
SUPPLIED_CONFIG_DIR="$WORK/operator-config"
mkdir -p "$SUPPLIED_CONFIG_DIR"

(
    unset CLAUDE_CONFIG_DIR || true
    LOCAL_RUN=true
    LOCAL_RUN_CONFIG_DIR="$SUPPLIED_CONFIG_DIR"
    export LOCAL_RUN LOCAL_RUN_CONFIG_DIR
    setup_local_run_isolation
    if [ "$CLAUDE_CONFIG_DIR" = "$SUPPLIED_CONFIG_DIR" ]; then
        echo "PASS_SUPPLIED_DIR_HONORED"
    else
        echo "FAIL_SUPPLIED_DIR_NOT_HONORED=$CLAUDE_CONFIG_DIR"
    fi
    # shellcheck disable=SC2153 # PREFLIGHT_STATE_DIR is assigned by
    # setup_local_run_isolation() above, not a typo of PREFLIGHT_STATE_HIT.
    case "$PREFLIGHT_STATE_DIR" in
        "$SUPPLIED_CONFIG_DIR"*)
            echo "FAIL_PREFLIGHT_STATE_DIR_INSIDE_SUPPLIED_CONFIG"
            ;;
        "$LOCAL_RUN_SCRATCH_DIR"*)
            echo "PASS_PREFLIGHT_STATE_DIR_UNDER_FRESH_ROOT"
            ;;
        *)
            echo "FAIL_PREFLIGHT_STATE_DIR_UNEXPECTED=$PREFLIGHT_STATE_DIR"
            ;;
    esac
) > "$WORK/part5a.out" 2>/dev/null

if grep -q "^PASS_SUPPLIED_DIR_HONORED$" "$WORK/part5a.out"; then
    pass "LOCAL_RUN_CONFIG_DIR is honored: CLAUDE_CONFIG_DIR is set to the supplied existing dir"
else
    fail "LOCAL_RUN_CONFIG_DIR was not honored ($(cat "$WORK/part5a.out"))"
fi
if grep -q "^PASS_PREFLIGHT_STATE_DIR_UNDER_FRESH_ROOT$" "$WORK/part5a.out"; then
    pass "with LOCAL_RUN_CONFIG_DIR set, PREFLIGHT_STATE_DIR still lives under the fresh per-run scratch root, not inside the supplied config dir"
else
    fail "with LOCAL_RUN_CONFIG_DIR set, PREFLIGHT_STATE_DIR is not under the fresh scratch root as expected ($(cat "$WORK/part5a.out"))"
fi

MISSING_CONFIG_DIR="$WORK/does-not-exist-config"
if (
    unset CLAUDE_CONFIG_DIR || true
    LOCAL_RUN=true
    LOCAL_RUN_CONFIG_DIR="$MISSING_CONFIG_DIR"
    export LOCAL_RUN LOCAL_RUN_CONFIG_DIR
    setup_local_run_isolation
) > "$WORK/part5b.out" 2>&1; then
    fail "LOCAL_RUN_CONFIG_DIR pointing at a non-existent path did not abort"
else
    pass "LOCAL_RUN_CONFIG_DIR pointing at a non-existent path hard-aborts (non-zero)"
fi
if grep -q "$MISSING_CONFIG_DIR" "$WORK/part5b.out"; then
    pass "the abort message names the missing LOCAL_RUN_CONFIG_DIR path"
else
    fail "the abort message did not name the missing path ($(cat "$WORK/part5b.out"))"
fi

FILE_NOT_DIR_CONFIG="$WORK/config-is-a-file"
: > "$FILE_NOT_DIR_CONFIG"
if (
    unset CLAUDE_CONFIG_DIR || true
    LOCAL_RUN=true
    LOCAL_RUN_CONFIG_DIR="$FILE_NOT_DIR_CONFIG"
    export LOCAL_RUN LOCAL_RUN_CONFIG_DIR
    setup_local_run_isolation
) > "$WORK/part5c.out" 2>&1; then
    fail "LOCAL_RUN_CONFIG_DIR pointing at a file (not a directory) did not abort"
else
    pass "LOCAL_RUN_CONFIG_DIR pointing at a file (not a directory) hard-aborts (non-zero)"
fi
if grep -q "$FILE_NOT_DIR_CONFIG" "$WORK/part5c.out"; then
    pass "the abort message names the file-not-directory LOCAL_RUN_CONFIG_DIR path"
else
    fail "the abort message did not name the file-not-directory path ($(cat "$WORK/part5c.out"))"
fi

(
    unset CLAUDE_CONFIG_DIR || true
    unset LOCAL_RUN_CONFIG_DIR || true
    LOCAL_RUN=true
    export LOCAL_RUN
    setup_local_run_isolation
    case "$CLAUDE_CONFIG_DIR" in
        "$LOCAL_RUN_SCRATCH_DIR"*)
            echo "PASS_DEFAULT_CONFIG_UNDER_SCRATCH"
            ;;
        *)
            echo "FAIL_DEFAULT_CONFIG_NOT_UNDER_SCRATCH=$CLAUDE_CONFIG_DIR"
            ;;
    esac
    case "$PREFLIGHT_STATE_DIR" in
        "$LOCAL_RUN_SCRATCH_DIR"*)
            echo "PASS_DEFAULT_PREFLIGHT_UNDER_SCRATCH"
            ;;
        *)
            echo "FAIL_DEFAULT_PREFLIGHT_NOT_UNDER_SCRATCH=$PREFLIGHT_STATE_DIR"
            ;;
    esac
) > "$WORK/part5d.out" 2>/dev/null

if grep -q "^PASS_DEFAULT_CONFIG_UNDER_SCRATCH$" "$WORK/part5d.out"; then
    pass "LOCAL_RUN_CONFIG_DIR unset: default fresh-mktemp CLAUDE_CONFIG_DIR behaviour is preserved"
else
    fail "LOCAL_RUN_CONFIG_DIR unset: default CLAUDE_CONFIG_DIR behaviour regressed ($(cat "$WORK/part5d.out"))"
fi
if grep -q "^PASS_DEFAULT_PREFLIGHT_UNDER_SCRATCH$" "$WORK/part5d.out"; then
    pass "LOCAL_RUN_CONFIG_DIR unset: PREFLIGHT_STATE_DIR still lives under the fresh per-run scratch root"
else
    fail "LOCAL_RUN_CONFIG_DIR unset: PREFLIGHT_STATE_DIR regressed ($(cat "$WORK/part5d.out"))"
fi

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
