#!/usr/bin/env bash
# changelog-section.sh — print the exact `## [X.Y.Z]` section from a
# Keep-a-Changelog file, for use as a GitHub Release body.
#
# Single source of truth for section extraction: BOTH the tag-triggered release
# workflow and the backfill script call this, so a release body always equals
# the CHANGELOG section byte-for-byte (dates preserved via the section header).
#
# Usage: changelog-section.sh X.Y.Z [CHANGELOG.md]
# Prints the section (header line included, so the date is preserved) up to but
# not including the next `## [` header. Interspersed reference-link definitions
# (`[X.Y.Z]: https://...`) are stripped, and leading/trailing blank lines are
# trimmed. Exits non-zero if the version has no section.
set -euo pipefail

version="${1:?usage: changelog-section.sh X.Y.Z [CHANGELOG.md]}"
changelog="${2:-CHANGELOG.md}"

test -f "$changelog" || { echo "changelog-section: $changelog not found" >&2; exit 2; }

section="$(
  awk -v ver="$version" '
    # Start capturing at "## [ver]"; stop at the next "## [" header.
    $0 ~ ("^## \\[" ver "\\]") { grab=1; print; next }
    grab && /^## \[/           { exit }
    grab                        { print }
  ' "$changelog" \
  | grep -vE '^\[[0-9]+\.[0-9]+\.[0-9]+\]:' \
  | awk '
    # Trim leading blank lines, and collapse/drop trailing blank lines.
    { lines[NR]=$0 }
    END {
      start=1; while (start<=NR && lines[start]=="") start++
      end=NR;  while (end>=start && lines[end]=="")   end--
      for (i=start; i<=end; i++) print lines[i]
    }'
)"

if [ -z "$section" ]; then
  echo "changelog-section: no '## [$version]' section found in $changelog" >&2
  exit 1
fi

printf '%s\n' "$section"
