#!/usr/bin/env python3
"""Version/release drift gate (origin: #75).

Stdlib only (no pytest / no third-party deps); run directly:
    python3 release_gate.py

Bug this guards: a `plugin.json` `version` bump lands on `main` but the
matching `vX.Y.Z` git tag + published GitHub Release never gets created (or
gets created as a draft) — a manual step that has already slipped twice in one
day (0.16.4, 0.16.5). Nothing short of an automated check catches that; the
release-tag lesson landed as skill guidance in PR #62 (v0.11.2), but guidance
is advisory, not enforcement.

Why this runs on a SCHEDULE, not on push/PR: the tag + Release are created by
the operator AFTER a version-bump PR merges, so any push/PR-triggered check
would always fail on the merge commit itself (the tag/release cannot exist
yet at that point) — a permanently-red or permanently-skipped check is
useless. A daily scheduled run instead catches real drift (a bump that stayed
un-released more than a day) without ever being red-by-construction. See
`.github/workflows/release-gate.yml`.

Decision logic is factored into a pure function, `is_released()`, that takes
plain data (a tag list + a release list) so it is unit-testable OFFLINE, no
`gh`/`git`/network call required — see `test_release_gate.py`.

Usage:
    python3 release_gate.py [--manifest PATH] [--repo-root PATH]

    Reads `version` from `harnesses/claude-code/marc/.claude-plugin/plugin.json`
    (override with --manifest), then shells out to `git tag -l` and
    `gh release list` (GITHUB_TOKEN / `gh auth` must already be available —
    CI provides this automatically) to build the tag/release lists passed to
    `is_released()`. Exits non-zero with an actionable message when the
    version has no matching pushed tag and published (non-draft) release.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ReleaseInfo:
    """One row of `gh release list --json tagName,isDraft`."""
    tag_name: str
    is_draft: bool


@dataclass
class GateResult:
    version: str
    expected_tag: str
    tag_found: bool
    release_found: bool
    ok: bool
    reason: str


def is_released(version: str, tags: list[str], releases: list[ReleaseInfo]) -> GateResult:
    """Pure decision function: given a plugin `version` (e.g. "0.16.6"), the
    full list of git tag names, and the full list of GitHub releases (tag name
    + draft flag), decide whether that version is properly released.

    A version is "released" iff:
      1. a tag `vX.Y.Z` (== "v" + version) is present in `tags`, AND
      2. a release for that same tag is present in `releases` and is NOT a
         draft (`is_draft` False).

    No I/O, no `gh`/`git` calls — safe to unit-test with synthetic fixtures.
    """
    expected_tag = f"v{version}"
    tag_found = expected_tag in tags

    matching_release = next((r for r in releases if r.tag_name == expected_tag), None)
    release_found = matching_release is not None and not matching_release.is_draft

    if tag_found and release_found:
        return GateResult(version, expected_tag, tag_found, release_found, True,
                           f"version {version} has tag {expected_tag} and a published release")

    if not tag_found:
        reason = (
            f"plugin.json version is {version} but no git tag {expected_tag} exists — "
            f"push it: git -c tag.gpgsign=false tag {expected_tag} && git push origin {expected_tag}"
        )
    elif matching_release is None:
        reason = (
            f"tag {expected_tag} exists but no GitHub Release references it — "
            f"create one: gh release create {expected_tag} --title {expected_tag} "
            f"--notes-file <changelog-section>"
        )
    else:
        reason = (
            f"tag {expected_tag} exists and a Release references it, but the Release "
            f"is still a DRAFT — publish it: gh release edit {expected_tag} --draft=false"
        )

    return GateResult(version, expected_tag, tag_found, release_found, False, reason)


def read_manifest_version(manifest_path: Path) -> str:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not version:
        raise ValueError(f"manifest {manifest_path} has no 'version' field")
    return version


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(cmd)}")
    return result.stdout


def fetch_tags(repo_root: Path) -> list[str]:
    raw = _run(["git", "-C", str(repo_root), "tag", "-l"])
    return [line.strip() for line in raw.splitlines() if line.strip()]


def fetch_releases() -> list[ReleaseInfo]:
    raw = _run(["gh", "release", "list", "--limit", "200", "--json", "tagName,isDraft"])
    payload = json.loads(raw)
    return [ReleaseInfo(tag_name=r["tagName"], is_draft=bool(r.get("isDraft"))) for r in payload]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=".", help="repo root for git tag lookups (default: cwd)")
    parser.add_argument(
        "--manifest", default=None,
        help="override plugin.json path (default: <repo-root>/harnesses/claude-code/marc/.claude-plugin/plugin.json)",
    )
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    manifest_path = Path(args.manifest) if args.manifest else (
        repo_root / "harnesses" / "claude-code" / "marc" / ".claude-plugin" / "plugin.json"
    )

    try:
        version = read_manifest_version(manifest_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
        print(f"::error::release_gate: could not read version from {manifest_path}: {e}")
        return 1

    try:
        tags = fetch_tags(repo_root)
    except Exception as e:  # noqa: BLE001
        print(f"::error::release_gate: git tag lookup failed: {e}")
        return 1

    try:
        releases = fetch_releases()
    except Exception as e:  # noqa: BLE001
        print(f"::error::release_gate: gh release list failed: {e}")
        return 1

    result = is_released(version, tags, releases)
    if result.ok:
        print(f"release_gate: OK — {result.reason}")
        return 0

    print(f"::error::release_gate: {result.reason}")
    print(
        f"release_gate: FAIL — plugin.json version={result.version} "
        f"expected_tag={result.expected_tag} tag_found={result.tag_found} "
        f"published_release_found={result.release_found}"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
