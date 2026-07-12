#!/usr/bin/env bash
# mARC :: PostToolUse automatic runaway-loop guard (origin: #71)
# ---------------------------------------------------------------------------
# Warn-only, non-blocking follow-up to the manual token sentinel (#69). While a
# runaway tool-loop is happening on an Opus-tier model, nudge the session (once
# per threshold band per turn) toward `/compact` or a Sonnet drop — so users who
# never run the manual diagnostic are still protected.
#
# CONTRACT: warn-only. This hook NEVER blocks, denies, or aborts a tool call.
# Every path exits 0. The advisory is emitted as a non-blocking Claude Code
# PostToolUse payload (hookSpecificOutput.additionalContext + systemMessage) by
# the shared Python logic — NO `decision`, NO exit 2. Missing python / missing
# transcript / any error => silent no-op. Reads the hook JSON on stdin and
# forwards it verbatim to the shared sentinel implementation.
# ---------------------------------------------------------------------------
set -u

SENTINEL="${CLAUDE_PLUGIN_ROOT:-}/scripts/token_sentinel.py"

# No plugin root / no script / no python => nothing to do. Stay silent, exit 0.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ] || [ ! -f "$SENTINEL" ]; then
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

python3 "$SENTINEL" --hook 2>/dev/null || true
exit 0
