#!/usr/bin/env bash
# mARC :: PostToolUse opportunistic >=7-day version re-check (origin: #52)
# ---------------------------------------------------------------------------
# outdated-check.sh only runs at SessionStart, so a chat alive for >=7 days
# never re-checks for a newer plugin version. This hook piggybacks on
# PostToolUse to opportunistically re-run the SAME check once a persisted
# last-run timestamp is at least 7 days old.
#
# The gate is read BEFORE any network call: on every other tool call (the
# overwhelming majority) this hook does a single local file stat and exits —
# no added latency, no network traffic. Only once the interval has elapsed
# does it fall through to the shared fetch/compare helper.
#
# Kept as its OWN hook (not folded into token-guard.sh) so the token sentinel
# stays isolated from this unrelated, lower-frequency concern.
#
# CONTRACT: warn-only, identical to outdated-check.sh. Every path exits 0.
# Offline / no-jq / no-curl / rate-limit / hung TLS => silent no-op.
# Anti-nag: nudges on major/minor drift only (patch ignored) — same shared
# helper, same rule.
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

# Gate FIRST: local-only, no network unless due. Interval overridable (tests).
marc_version_check_due "${MARC_VERSION_CHECK_INTERVAL:-604800}" || exit 0

marc_version_check_report "$PLUGIN_JSON"
marc_version_check_mark_run

exit 0
