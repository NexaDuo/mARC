#!/usr/bin/env bash
# mARC :: opt-in per-turn token-cost telemetry recorder (Stop hook, origin: #149)
# ---------------------------------------------------------------------------
# OPT-IN, DEFAULT OFF. This hook is wired on every Stop event but writes
# NOTHING unless the current repo's `.claude/team.toml` has
#   [telemetry]
#   enabled = true
# Absent team.toml, absent [telemetry] section, `enabled = false`, or any
# other value -> silent no-op, exit 0. The toggle is checked HERE in bash
# (zero-dependency key-anchored `sed`, same convention as the rest of the
# plugin's team.toml extraction) so the common case (telemetry never turned
# on) never even shells out to python3 — cheap by construction.
#
# CONTRACT: warn-only, exit 0 on every path. This hook NEVER blocks, denies,
# or aborts a turn, and never prints anything a user would mistake for an
# error. Only NUMERIC usage metadata is ever recorded — see
# scripts/token_telemetry.py's module docstring for the full privacy
# boundary; message/prompt content is never read into the output file.
# ---------------------------------------------------------------------------
set -u

TEAM_TOML="${CLAUDE_PROJECT_DIR:-$PWD}/.claude/team.toml"

[ -f "$TEAM_TOML" ] || exit 0

# Same zero-dependency key-anchored extraction as docs/team.toml.example's
# schema contract test: match the literal key name `enabled` anywhere in the
# file (key names are unique file-wide by convention, CI-enforced).
enabled="$(sed -n 's/^[[:space:]]*enabled[[:space:]]*=[[:space:]]*"\{0,1\}\([^"#[:space:]]*\)"\{0,1\}.*/\1/p' "$TEAM_TOML" 2>/dev/null | tail -n1)"
[ "$enabled" = "true" ] || exit 0

RECORDER="${CLAUDE_PLUGIN_ROOT:-}/scripts/token_telemetry.py"

# No plugin root / no script / no python => nothing to do. Stay silent, exit 0.
if [ -z "${CLAUDE_PLUGIN_ROOT:-}" ] || [ ! -f "$RECORDER" ]; then
  exit 0
fi
if ! command -v python3 >/dev/null 2>&1; then
  exit 0
fi

python3 "$RECORDER" --hook 2>/dev/null || true
exit 0
