#!/usr/bin/env bash
# backfill-releases.sh — one-time, idempotent backfill of tags + GitHub Releases
# for v0.1.0..v0.8.0 (issue #26).
#
# Why a script (not just workflow tag pushes): each tag must point at the
# HISTORICAL commit that FIRST set that version in plugin.json — derived by
# reading plugin.json at each commit, NOT by tagging HEAD. Those historical
# commits predate this extractor and the later CHANGELOG sections, so the
# release body is taken from the CURRENT CHANGELOG.md (which has every section),
# while the TAG points at the historical commit. The tag-triggered release.yml
# workflow handles all FUTURE releases.
#
# Idempotent + reproducible: re-running updates existing releases instead of
# failing, and asserts each commit actually carries its claimed version before
# tagging (fail-closed) so a wrong mapping can never mis-tag prod metadata.
#
# Requires: gh (authenticated with contents:write), git, jq. Run from anywhere
# in the repo. Set REMOTE (default: origin) if your push remote differs.
#
# DRY_RUN=1 verifies the mapping and prints what WOULD be tagged/released
# (tag -> commit + the resolved release body) without creating or pushing
# anything — use it to review the backfill before running it for real.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
CHANGELOG=CHANGELOG.md
PLUGIN=harnesses/claude-code/marc/.claude-plugin/plugin.json
REMOTE="${REMOTE:-origin}"
DRY_RUN="${DRY_RUN:-0}"
HERE="$(dirname "$0")"

# version <space> historical commit that set it (derived: `git show <sha>:PLUGIN
# | jq .version` is the first commit where X.Y.Z appears). Verified below.
MAPPING=$(cat <<'MAP'
0.1.0 5476c3dc55be68391c650fbc76b858d88a630528
0.2.0 bc0390e4abc534ecc5198e864b535044cd20a8fc
0.3.0 8455b20d1ac86893b256892c465c3a518870d473
0.4.0 ca4da492677ae239008ec9a48f7fb1d786370c63
0.5.0 fc6affd4152193de469a72c03e9f323fb734c204
0.6.0 fa77915a827c29f4849d556b38bdab857fa89827
0.7.0 b6ab4b9c1d90c50be4d59688d1a767e550dc4c61
0.8.0 96086ba7d402f8565bb731f688d3368a2d89e039
MAP
)

while read -r version sha; do
  [ -n "$version" ] || continue
  tag="v${version}"

  # FAIL-CLOSED verify: the mapped commit must actually set this version.
  got="$(git show "${sha}:${PLUGIN}" | jq -r .version)"
  if [ "$got" != "$version" ]; then
    echo "::error::mapping mismatch: ${sha} has plugin.json version '${got}', expected '${version}'" >&2
    exit 1
  fi

  # Extract the release body from the CURRENT CHANGELOG (has every section).
  notes="$(mktemp)"
  bash "${HERE}/changelog-section.sh" "$version" "$CHANGELOG" > "$notes"

  if [ "$DRY_RUN" = "1" ]; then
    echo "── ${tag} -> ${sha} (verified plugin.json=${got}) ─────────────"
    echo "   release body ($(wc -l < "$notes") lines):"
    sed 's/^/   | /' "$notes"
    rm -f "$notes"
    continue
  fi

  # Create the tag at the historical commit if missing, and push it.
  if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    echo "tag ${tag} already exists locally at $(git rev-parse "${tag}")"
  else
    git tag "$tag" "$sha"
    echo "created local tag ${tag} -> ${sha}"
  fi
  git push "$REMOTE" "refs/tags/${tag}" || echo "  (tag ${tag} already on ${REMOTE})"

  # Idempotent release publish/update.
  if gh release view "$tag" >/dev/null 2>&1; then
    gh release edit "$tag" --title "$tag" --notes-file "$notes"
    echo "updated release ${tag}"
  else
    gh release create "$tag" --title "$tag" --notes-file "$notes" --target "$sha" --verify-tag
    echo "created release ${tag} (target ${sha})"
  fi
  rm -f "$notes"
done <<< "$MAPPING"

echo "backfill complete: v0.1.0..v0.8.0 tags + Releases present."
