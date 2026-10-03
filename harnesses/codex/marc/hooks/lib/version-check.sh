#!/usr/bin/env bash
# mARC :: shared version-check helper (origin: #52)
# ---------------------------------------------------------------------------
# Sourced (not executed) by both hook entry points that need to nudge on an
# outdated installed plugin version:
#   * hooks/outdated-check.sh   (SessionStart, unconditional per session)
#   * hooks/outdated-recheck.sh (PostToolUse, gated to >=7 days since last run)
#
# Keeping the fetch + semver-compare logic here means both entry points share
# ONE implementation instead of duplicating the major/minor comparison.
#
# CONTRACT: every function here is fail-silent by design (bare `return 0` on
# any error path) so a sourcing caller with `set -u` + `trap 'exit 0' ERR`
# degrades to a no-op on offline / missing-tool / malformed-json conditions.
# This file does not set its own traps since it is sourced, not executed.
# ---------------------------------------------------------------------------

# State dir: user-writable, survives plugin updates (explicitly NOT
# team.toml, NOT under CLAUDE_PLUGIN_ROOT — that cache is read-only and wiped
# on every plugin update). Project-agnostic on purpose so it works the same
# in any repo. Overridable for tests via MARC_STATE_DIR.
MARC_STATE_DIR="${MARC_STATE_DIR:-$HOME/.claude/marc-state}"
MARC_VERSION_CHECK_STAMP="$MARC_STATE_DIR/outdated-check-last-run"

# marc_version_check_due [interval_seconds]
# Local-only (no network): returns success (0) when the persisted last-run
# timestamp is missing, unreadable, or at least `interval_seconds` old;
# returns failure (1) when the gate has not yet elapsed. Defaults to 7 days.
marc_version_check_due() {
  interval="${1:-604800}"
  [ -f "$MARC_VERSION_CHECK_STAMP" ] || return 0
  last="$(cat "$MARC_VERSION_CHECK_STAMP" 2>/dev/null)" || return 0
  case "$last" in ''|*[!0-9]*) return 0 ;; esac
  now="$(date +%s 2>/dev/null)" || return 0
  elapsed=$(( now - last ))
  [ "$elapsed" -ge "$interval" ]
}

# marc_version_check_mark_run
# Persist "now" as the last-run timestamp. Best-effort; any failure to
# create the state dir or write the file is silently swallowed.
marc_version_check_mark_run() {
  mkdir -p "$MARC_STATE_DIR" 2>/dev/null || return 0
  date +%s > "$MARC_VERSION_CHECK_STAMP" 2>/dev/null || true
  return 0
}

# marc_version_check_report <plugin_json_path>
# Fetches the remote plugin.json version (main branch) and prints ONE nudge
# line to stdout if the remote MAJOR or MINOR is ahead of the installed one
# (patch-only bumps are ignored — anti-nag). Silent no-op on any failure:
# missing file, missing jq, offline, rate-limited, hung TLS (bounded via
# timeout), or malformed semver on either side.
marc_version_check_report() {
  plugin_json="$1"
  [ -f "$plugin_json" ] || return 0
  command -v jq >/dev/null 2>&1 || return 0

  installed="$(jq -r '.version // empty' "$plugin_json" 2>/dev/null)" || return 0
  [ -n "$installed" ] || return 0

  remote_url="${MARC_REMOTE_PLUGIN_JSON_URL:-https://raw.githubusercontent.com/NexaDuo/mARC/main/harnesses/claude-code/marc/.claude-plugin/plugin.json}"

  remote_json=""
  if command -v curl >/dev/null 2>&1; then
    remote_json="$(timeout 5 curl -fsSL --max-time 3 "$remote_url" 2>/dev/null)" || return 0
  elif command -v wget >/dev/null 2>&1; then
    remote_json="$(timeout 5 wget -qO- --timeout=3 "$remote_url" 2>/dev/null)" || return 0
  else
    return 0
  fi
  [ -n "$remote_json" ] || return 0

  remote="$(printf '%s' "$remote_json" | jq -r '.version // empty' 2>/dev/null)" || return 0
  [ -n "$remote" ] || return 0

  # Both must be dotted numeric semver; anything else => no-op (defensive).
  case "$installed" in ""|*[!0-9.]*) return 0 ;; esac
  case "$remote"    in ""|*[!0-9.]*) return 0 ;; esac

  # Extract major/minor (default missing parts to 0).
  i_major="${installed%%.*}"; i_rest="${installed#*.}"; i_minor="${i_rest%%.*}"
  r_major="${remote%%.*}";    r_rest="${remote#*.}";    r_minor="${r_rest%%.*}"
  [ "$i_minor" = "$installed" ] && i_minor=0
  [ "$r_minor" = "$remote" ]    && r_minor=0
  : "${i_major:=0}" "${i_minor:=0}" "${r_major:=0}" "${r_minor:=0}"

  # Numeric-only guard so the integer comparisons below can't blow up.
  case "$i_major$i_minor$r_major$r_minor" in *[!0-9]*) return 0 ;; esac

  behind=0
  if [ "$r_major" -gt "$i_major" ]; then
    behind=1
  elif [ "$r_major" -eq "$i_major" ] && [ "$r_minor" -gt "$i_minor" ]; then
    behind=1
  fi

  if [ "$behind" -eq 1 ]; then
    # Backticks below are literal markdown, not command substitution.
    # shellcheck disable=SC2016
    printf '[mARC] update available: %s -> %s. Update: `claude plugin update marc@nexaduo` (or in-app: `/plugin marketplace update nexaduo` then `/reload-plugins`). Enable marketplace auto-update to stop seeing this.\n' \
      "$installed" "$remote"
  fi
  return 0
}
