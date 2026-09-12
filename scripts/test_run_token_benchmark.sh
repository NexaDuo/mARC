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
hash1="$(compute_task_hash "$v1" "claude-sonnet-5" "read core/scripts/board.py and output a summary")"

write_claude_stub "2.2.0"
v2="$(PATH="$CLAUDE_BIN" resolve_claude_version)"
hash2="$(compute_task_hash "$v2" "claude-sonnet-5" "read core/scripts/board.py and output a summary")"

if [ "$v1" != "$v2" ] && [ "$hash1" != "$hash2" ]; then
    pass "post-install capture differs across CLI versions ('$v1' vs '$v2') and so does the hash ('$hash1' vs '$hash2') -- drift guard is live"
else
    fail "post-install capture/hash did not differ across CLI versions (v1='$v1' v2='$v2' hash1='$hash1' hash2='$hash2')"
fi

# 2c. A `claude --version` failure AFTER install must fail loudly, not hash
# a placeholder.
if PATH="$EMPTY_BIN" resolve_claude_version > /dev/null 2>&1; then
    fail "resolve_claude_version did not fail when claude --version fails post-install"
else
    pass "resolve_claude_version fails loudly (non-zero) instead of hashing a placeholder"
fi

echo
if [ "$FAILS" -eq 0 ]; then
    echo "All checks passed."
    exit 0
else
    echo "$FAILS check(s) FAILED."
    exit 1
fi
