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
# ---------------------------------------------------------------------------
set -u

# Belt-and-suspenders: any unexpected failure degrades to a silent no-op.
trap 'exit 0' ERR

PLUGIN_JSON="${CLAUDE_PLUGIN_ROOT:-}/.claude-plugin/plugin.json"
[ -n "${CLAUDE_PLUGIN_ROOT:-}" ] && [ -f "$PLUGIN_JSON" ] || exit 0

# jq is needed to parse both manifests; absent => no-op.
command -v jq >/dev/null 2>&1 || exit 0

installed="$(jq -r '.version // empty' "$PLUGIN_JSON" 2>/dev/null)" || exit 0
[ -n "$installed" ] || exit 0

# Remote version comes from plugin.json ON MAIN (raw GitHub), NOT GH Releases.
REMOTE_URL="https://raw.githubusercontent.com/NexaDuo/mARC/main/harnesses/claude-code/marc/.claude-plugin/plugin.json"

# Explicit short timeout on the network call; timeout(1) wraps the fetcher so
# even a hung TLS handshake cannot stall the session. curl preferred, wget fallback.
remote_json=""
if command -v curl >/dev/null 2>&1; then
  remote_json="$(timeout 5 curl -fsSL --max-time 3 "$REMOTE_URL" 2>/dev/null)" || exit 0
elif command -v wget >/dev/null 2>&1; then
  remote_json="$(timeout 5 wget -qO- --timeout=3 "$REMOTE_URL" 2>/dev/null)" || exit 0
else
  exit 0
fi
[ -n "$remote_json" ] || exit 0

remote="$(printf '%s' "$remote_json" | jq -r '.version // empty' 2>/dev/null)" || exit 0
[ -n "$remote" ] || exit 0

# Both must be dotted numeric semver; anything else => no-op (defensive).
case "$installed" in ""|*[!0-9.]*) exit 0 ;; esac
case "$remote"    in ""|*[!0-9.]*) exit 0 ;; esac

# Extract major/minor (default missing parts to 0).
i_major="${installed%%.*}"; i_rest="${installed#*.}"; i_minor="${i_rest%%.*}"
r_major="${remote%%.*}";    r_rest="${remote#*.}";    r_minor="${r_rest%%.*}"
[ "$i_minor" = "$installed" ] && i_minor=0
[ "$r_minor" = "$remote" ]    && r_minor=0
: "${i_major:=0}" "${i_minor:=0}" "${r_major:=0}" "${r_minor:=0}"

# Numeric-only guard so the integer comparisons below can't blow up.
case "$i_major$i_minor$r_major$r_minor" in *[!0-9]*) exit 0 ;; esac

behind=0
if [ "$r_major" -gt "$i_major" ]; then
  behind=1
elif [ "$r_major" -eq "$i_major" ] && [ "$r_minor" -gt "$i_minor" ]; then
  behind=1
fi

if [ "$behind" -eq 1 ]; then
  printf '[mARC] update available: %s -> %s. Update: `claude plugin update marc@nexaduo` (or in-app: `/plugin marketplace update nexaduo` then `/reload-plugins`). Enable marketplace auto-update to stop seeing this.\n' \
    "$installed" "$remote"
fi

exit 0
