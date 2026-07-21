#!/usr/bin/env bash
# mARC :: SessionStart safety-net hook
# ---------------------------------------------------------------------------
# Warn (exactly one line, into context) when the INSTALLED plugin version is
# behind the version on the repo's `main`, so users with marketplace
# auto-update OFF don't silently miss fixes.
#
# CONTRACT: warn-only. This hook NEVER blocks or fails a session. Every path
# exits 0. Offline / error / rate-limit / missing tool => silent no-op.
# Anti-nag: only nudges when the MAJOR or MINOR differs (patch bumps are
# ignored, so a routine patch release does not pester every session).
#
# The actual fetch/compare logic lives in lib/version-check.sh, shared with
# hooks/outdated-recheck.sh (the >=7-day opportunistic PostToolUse re-check,
# origin #52) so the semver-compare logic exists in exactly one place.
# ---------------------------------------------------------------------------
set -u

# Belt-and-suspenders: any unexpected failure degrades to a silent no-op.
trap 'exit 0' ERR

PLUGIN_JSON="${CLAUDE_PLUGIN_ROOT:-}/.claude-plugin/plugin.json"
[ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$PLUGIN_JSON" ] || exit 0

LIB_DIR="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 0
# shellcheck source=lib/version-check.sh
# shellcheck disable=SC1091
. "$LIB_DIR/lib/version-check.sh" || exit 0

marc_version_check_report "$PLUGIN_JSON"
# Record that a check just ran so the PostToolUse re-check (outdated-recheck.sh)
# doesn't fire again until its own >=7-day gate elapses from here.
marc_version_check_mark_run

exit 0
