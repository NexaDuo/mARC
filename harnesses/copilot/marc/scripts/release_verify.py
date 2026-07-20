#!/usr/bin/env python3
"""Release-verification operator script (origin: #113).

ONE-call replacement for the `@techlead` skill's hand-rolled post-release
`gh` sequence (does the tag exist? did the tag-triggered `Release` workflow
run go green? did GitHub Release publish and get marked Latest?) that
`release_gate.py` (#75) deliberately does NOT cover — `release_gate.py`
answers "is this version released at all" on a daily schedule; this script
answers "did THIS release just go through cleanly, right now" on demand,
right after a tag push, with the workflow-run result included.

Usage:
    python3 release_verify.py [VERSION] [--json] [--repo-root PATH]
                               [--team-toml PATH] [--repo OWNER/REPO]
                               [--workflow NAME]

    VERSION   defaults to the version in
              <repo-root>/harnesses/claude-code/marc/.claude-plugin/plugin.json
              (override with an explicit positional arg, e.g. "0.16.7" or
              "v0.16.7" — the leading "v" is optional either way).
    --repo    OWNER/REPO override; default resolution mirrors
              `board_reconcile.py`: team.toml `gh_repo`, then `gh repo view`
              zero-config fallback.
    --workflow  the tag-triggered release workflow's file name (default:
              "release.yml", this repo's actual workflow — override for a
              consumer repo with a differently named release workflow).

Checks (all three must pass for `ok: true`):
  1. tag_exists       — `vX.Y.Z` tag exists in the repo (via `gh api
                         .../git/refs/tags/vX.Y.Z`, no local clone required).
  2. workflow_run      — the tag-triggered release workflow has a run for
                         this tag with `status == completed` and
                         `conclusion == success` (via `gh run list
                         --workflow <name> --branch vX.Y.Z`).
  3. release_published — a GitHub Release exists for the tag, is NOT a
                         draft, and IS marked "Latest" (via `gh release
                         list --json ...,isLatest`).

Decision logic is a pure function, `verify_release()`, taking plain data (no
I/O) so it's unit-testable offline — see `test_release_verify.py`, mirroring
`release_gate.py`'s `is_released()` pattern.

Never crashes: every `gh`/`git` call is wrapped, a failure degrades to a
`None`/`False` field plus a note in the check's `notes`, not an exception —
same discipline as `board_reconcile.py reconcile`.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from board_reconcile import RepoConfig, _resolve_config  # noqa: E402

SCHEMA_VERSION = 1
DEFAULT_WORKFLOW = "release.yml"


@dataclass
class WorkflowRun:
    """One row of `gh run list --json status,conclusion,headBranch,url`."""
    status: Optional[str]
    conclusion: Optional[str]
    url: Optional[str]
    created_at: Optional[str] = None


@dataclass
class ReleaseRow:
    """One row of `gh release list --json tagName,isDraft,isLatest`."""
    tag_name: str
    is_draft: bool
    is_latest: bool


@dataclass
class CheckResult:
    ok: bool
    notes: str


@dataclass
class VerifyResult:
    schema_version: int
    version: str
    expected_tag: str
    tag_exists: CheckResult
    workflow_run: CheckResult
    release_published: CheckResult
    ok: bool

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


def _normalize_version(version: str) -> str:
    return version.lstrip("v")


def verify_release(
    version: str,
    tag_exists: bool,
    runs: list[WorkflowRun],
    releases: list[ReleaseRow],
) -> VerifyResult:
    """Pure decision function — no I/O. `runs` and `releases` are assumed
    already filtered to the relevant tag/workflow by the caller (mirrors
    `release_gate.py::is_released`'s separation of fetch vs. decide)."""
    norm_version = _normalize_version(version)
    expected_tag = f"v{norm_version}"

    tag_check = CheckResult(
        ok=tag_exists,
        notes=f"tag {expected_tag} exists" if tag_exists else f"tag {expected_tag} not found",
    )

    if not runs:
        run_check = CheckResult(
            ok=False,
            notes=f"no release-workflow run found for tag {expected_tag} "
                  f"(has it been pushed yet?)",
        )
    else:
        # Most recent run wins (list is assumed newest-first, matching `gh run
        # list`'s default ordering).
        latest = runs[0]
        if latest.status != "completed":
            run_check = CheckResult(
                ok=False,
                notes=f"release-workflow run for {expected_tag} is still "
                      f"{latest.status!r} — not completed yet ({latest.url})",
            )
        elif latest.conclusion != "success":
            run_check = CheckResult(
                ok=False,
                notes=f"release-workflow run for {expected_tag} completed with "
                      f"conclusion {latest.conclusion!r}, not 'success' ({latest.url})",
            )
        else:
            run_check = CheckResult(
                ok=True,
                notes=f"release-workflow run for {expected_tag} completed successfully ({latest.url})",
            )

    matching_release = next((r for r in releases if r.tag_name == expected_tag), None)
    if matching_release is None:
        release_check = CheckResult(
            ok=False,
            notes=f"no GitHub Release found for tag {expected_tag}",
        )
    elif matching_release.is_draft:
        release_check = CheckResult(
            ok=False,
            notes=f"Release {expected_tag} exists but is still a DRAFT",
        )
    elif not matching_release.is_latest:
        release_check = CheckResult(
            ok=False,
            notes=f"Release {expected_tag} is published but NOT marked 'Latest' "
                  f"— a newer/different release may be marked Latest instead",
        )
    else:
        release_check = CheckResult(
            ok=True,
            notes=f"Release {expected_tag} is published and marked 'Latest'",
        )

    ok = tag_check.ok and run_check.ok and release_check.ok
    return VerifyResult(
        schema_version=SCHEMA_VERSION,
        version=norm_version,
        expected_tag=expected_tag,
        tag_exists=tag_check,
        workflow_run=run_check,
        release_published=release_check,
        ok=ok,
    )


# --- I/O: fetch real state via `gh`/`git` ------------------------------------

def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(cmd)}")
    return result.stdout


def read_manifest_version(manifest_path: Path) -> str:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"manifest not found: {manifest_path}")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    version = data.get("version")
    if not version:
        raise ValueError(f"manifest {manifest_path} has no 'version' field")
    return version


def fetch_tag_exists(repo: Optional[str], expected_tag: str) -> bool:
    if not repo:
        return False
    try:
        _run(["gh", "api", f"repos/{repo}/git/refs/tags/{expected_tag}"])
        return True
    except Exception:  # noqa: BLE001
        return False


def fetch_workflow_runs(repo: Optional[str], workflow: str, expected_tag: str) -> list[WorkflowRun]:
    cmd = ["gh", "run", "list", "--workflow", workflow, "--branch", expected_tag,
           "--json", "status,conclusion,url,createdAt", "--limit", "10"]
    if repo:
        cmd += ["--repo", repo]
    raw = _run(cmd)
    payload = json.loads(raw)
    return [
        WorkflowRun(
            status=r.get("status"),
            conclusion=r.get("conclusion"),
            url=r.get("url"),
            created_at=r.get("createdAt"),
        )
        for r in payload
    ]


def fetch_releases(repo: Optional[str]) -> list[ReleaseRow]:
    cmd = ["gh", "release", "list", "--limit", "200",
           "--json", "tagName,isDraft,isLatest"]
    if repo:
        cmd += ["--repo", repo]
    raw = _run(cmd)
    payload = json.loads(raw)
    return [
        ReleaseRow(
            tag_name=r["tagName"],
            is_draft=bool(r.get("isDraft")),
            is_latest=bool(r.get("isLatest")),
        )
        for r in payload
    ]


def render_human(result: VerifyResult) -> str:
    def mark(ok: bool) -> str:
        return "OK " if ok else "FAIL"

    lines = [
        f"release_verify {result.expected_tag} (version {result.version})",
        f"  [{mark(result.tag_exists.ok)}] tag_exists       — {result.tag_exists.notes}",
        f"  [{mark(result.workflow_run.ok)}] workflow_run     — {result.workflow_run.notes}",
        f"  [{mark(result.release_published.ok)}] release_published — {result.release_published.notes}",
        "",
        f"overall: {'OK' if result.ok else 'FAIL'}",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version", nargs="?", default=None,
                         help="version to verify (default: plugin.json's version); leading 'v' optional")
    parser.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    parser.add_argument("--repo-root", default=".", help="repo root for manifest/team.toml lookups (default: cwd)")
    parser.add_argument("--team-toml", default=None, help="override team.toml path")
    parser.add_argument("--repo", default=None, help="OWNER/REPO override (default: resolved like board_reconcile.py)")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW,
                         help=f"tag-triggered release workflow file name (default: {DEFAULT_WORKFLOW})")
    parser.add_argument("--manifest", default=None, help="override plugin.json path")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()

    version = args.version
    if version is None:
        manifest_path = Path(args.manifest) if args.manifest else (
            repo_root / "harnesses" / "claude-code" / "marc" / ".claude-plugin" / "plugin.json"
        )
        try:
            version = read_manifest_version(manifest_path)
        except (FileNotFoundError, ValueError, json.JSONDecodeError) as e:
            print(f"::error::release_verify: could not read version from {manifest_path}: {e}", file=sys.stderr)
            return 1

    repo = args.repo
    if not repo:
        ns = argparse.Namespace(repo_root=str(repo_root), team_toml=args.team_toml)
        config: RepoConfig = _resolve_config(ns)
        repo = config.gh_repo

    expected_tag = f"v{_normalize_version(version)}"

    tag_exists = fetch_tag_exists(repo, expected_tag)

    try:
        runs = fetch_workflow_runs(repo, args.workflow, expected_tag)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::release_verify: gh run list failed: {e}", file=sys.stderr)
        runs = []

    try:
        releases = fetch_releases(repo)
    except Exception as e:  # noqa: BLE001
        print(f"::warning::release_verify: gh release list failed: {e}", file=sys.stderr)
        releases = []

    result = verify_release(version, tag_exists, runs, releases)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_human(result))

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
