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
# DRY_RUN=1 verifies the mapping AND actually exercises tag creation (in a
# throwaway `refs/tags/dryrun-check/…` namespace that is immediately deleted),
# then prints the resolved release body — WITHOUT creating the real tag, pushing,
# or touching any Release. A green dry-run therefore proves `git tag` truly
# succeeds under the caller's git config; it does not merely print the command.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"
CHANGELOG=CHANGELOG.md
PLUGIN=harnesses/claude-code/marc/.claude-plugin/plugin.json
REMOTE="${REMOTE:-origin}"
DRY_RUN="${DRY_RUN:-0}"
HERE="$(dirname "$0")"

# Create tag $1 at commit $2 deterministically. `-c tag.gpgsign=false` overrides
# any user/global `tag.gpgsign true`: without it, git turns this into a SIGNED
# ANNOTATED tag that needs a GPG key + message and aborts with
# `fatal: no tag message?` — the reproducibility bug this fix closes. We create
# an ANNOTATED (`-a`) UNSIGNED tag with an explicit message (`-m`), which needs
# no key and behaves identically on every machine.
make_tag() {
  git -c tag.gpgsign=false tag -a "$1" -m "mARC $1" "$2"
}

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
    # Exercise the EXACT tag-creation codepath (make_tag) so this can never hide
    # a signing/config bug again. Use a throwaway namespace and delete it — the
    # real `refs/tags/${tag}` is never created and nothing is pushed.
    probe="dryrun-check/${tag}"
    git tag -d "$probe" >/dev/null 2>&1 || true
    make_tag "$probe" "$sha"
    git tag -d "$probe" >/dev/null
    echo "── ${tag} -> ${sha} (verified plugin.json=${got}) | git tag OK ─────────────"
    echo "   release body ($(wc -l < "$notes") lines):"
    sed 's/^/   | /' "$notes"
    rm -f "$notes"
    continue
  fi

  # Create the tag at the historical commit if missing, and push it.
  if git rev-parse -q --verify "refs/tags/${tag}" >/dev/null; then
    echo "tag ${tag} already exists locally at $(git rev-parse "${tag}")"
  else
    make_tag "$tag" "$sha"
    echo "created local tag ${tag} -> ${sha}"
  fi
  git push "$REMOTE" "refs/tags/${tag}" || echo "  (tag ${tag} already on ${REMOTE})"

  # Idempotent release publish/update.
  #
  # NOTE: NO `--target` here. The tag is already created + pushed above (via the
  # git remote, which for SSH is exempt from OAuth scopes), so it already pins
  # the historical commit. Passing `--target <sha>` makes `gh` try to CREATE the
  # tag ref itself over the API — and creating a ref at a commit that contains
  # `.github/workflows/` files (true for v0.2.0+) requires the token's
  # `workflow` OAuth scope. A plain `repo`-scoped token then fails with
  # "workflow scope may be required". `--verify-tag` alone (tag must already
  # exist) both asserts our push landed and needs no `workflow` scope.
  if gh release view "$tag" >/dev/null 2>&1; then
    gh release edit "$tag" --title "$tag" --notes-file "$notes"
    echo "updated release ${tag}"
  else
    gh release create "$tag" --title "$tag" --notes-file "$notes" --verify-tag
    echo "created release ${tag} -> ${sha}"
  fi
  rm -f "$notes"
done <<< "$MAPPING"

echo "backfill complete: v0.1.0..v0.8.0 tags + Releases present."
