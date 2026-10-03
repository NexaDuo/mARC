#!/usr/bin/env bash
# mARC :: SessionStart(source=compact) operating-invariants re-injection (origin: #41, #145)
# ---------------------------------------------------------------------------
# Per the #41 decision: restating rules after compaction recovers adherence,
# but a periodic/blanket re-injection is the wrong shape (no native
# turn-count hook exists, and precedent favors narrow, event-triggered
# reminders over full-card spam). Compaction is precisely when premises are
# most at risk of being summarized away, and SessionStart(source=compact) is
# the DOCUMENTED, reliable carrier for post-compaction context injection
# (verified against the official hooks docs: PostCompact has no decision
# control / additionalContext support — it cannot inject text at all).
#
# The hooks.json matcher is already gated to "compact" so this script does
# not run at all on startup/resume/clear/fork SessionStart sources. The
# `source` check below is belt-and-suspenders in case this script is ever
# invoked with a looser matcher (defense in depth, same posture as the other
# hooks in this directory).
#
# CONTRACT: warn-only / inject-only. This hook NEVER blocks or fails a
# session. Every path exits 0. A missing or unreadable card file degrades
# silently (no error, no partial output) — the card is a soft reminder, not
# a gate.
# ---------------------------------------------------------------------------
set -u

# Belt-and-suspenders: any unexpected failure degrades to a silent no-op.
trap 'exit 0' ERR

input="$(cat 2>/dev/null)" || exit 0

# Defensive re-check of `source` even though hooks.json matcher already
# restricts invocation to the "compact" source (see header comment above).
source_field=""
if command -v jq >/dev/null 2>&1; then
  source_field="$(printf '%s' "$input" | jq -r '.source // empty' 2>/dev/null)" || source_field=""
fi
if [ -n "$source_field" ] && [ "$source_field" != "compact" ]; then
  exit 0
fi

SCRIPT_DIR="$(CDPATH="" cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)" || exit 0
CARD="${MARC_INVARIANTS_CARD:-$SCRIPT_DIR/../skills/tech-lead/references/invariants-card.md}"

[ -f "$CARD" ] && [ -r "$CARD" ] || exit 0

cat "$CARD" 2>/dev/null || exit 0

exit 0
