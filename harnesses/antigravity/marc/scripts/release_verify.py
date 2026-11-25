#!/usr/bin/env python3
"""Release-verification operator script (origin: #113).

ONE-call replacement for the `@techlead` skill's hand-rolled post-release
`gh` sequence (does the tag exist? did the release workflow
run go green? did GitHub Release publish and get marked Latest?) that
`release_gate.py` (#75) deliberately does NOT cover — `release_gate.py`
answers "is this version released at all" on a daily schedule; this script
answers "did THIS release just go through cleanly, right now" on demand,
right after release automation runs, with the workflow-run result included.

Usage:
    python3 release_verify.py [VERSION] [--json] [--repo-root PATH]
                               [--team-toml PATH] [--repo OWNER/REPO]
                               [--workflow NAME]

    VERSION   defaults to the version in
              <repo-root>/harnesses/claude-code/marc/.claude-plugin/plugin.json
              (override with an explicit positional arg, e.g. "0.16.7" or
              "v0.16.7" — the leading "v" is optional either way).
    --repo    OWNER/REPO override; default resolution mirrors
              `board.py`: team.toml `gh_repo`, then `gh repo view`
              zero-config fallback.
    --workflow  the release workflow's file name (default:
              "release.yml", this repo's actual workflow — override for a
              consumer repo with a differently named release workflow).

Checks (all applicable checks must pass for `ok: true`):
  1. tag_exists       — `vX.Y.Z` tag exists in the repo (via `gh api
                         .../git/refs/tags/vX.Y.Z`, no local clone required).
  2. workflow_run      — the release workflow has a successful completed run
                         for the tag commit (push on main or dispatch). Dispatch
                         can run from later main commits with the same manifest
                         version. Tag-branch lookup remains a consumer fallback.
  3. release_published — a GitHub Release exists for the tag, is NOT a
                         draft, and IS marked "Latest" (via `gh release
                         list --json ...,isLatest`).

  4. npm_published    — only in the launcher source repo, for tags >=
                         v26.11.26: mapped npm version exists, is latest, has
                         provenance (except the exact bootstrap tag), and its
                         tarball SHA matches the locally peeled tag commit.
                         Consumer repos and older tags are not_applicable.

Decision logic is a pure function, `verify_release()`, taking plain data (no
I/O) so it's unit-testable offline — see `test_release_verify.py`, mirroring
`release_gate.py`'s `is_released()` pattern.

Never crashes: every `gh`/`git` call is wrapped, a failure degrades to a
`None`/`False` field plus a note in the check's `notes`, not an exception —
same discipline as `board.py reconcile`.
"""
from __future__ import annotations

import argparse
import json
import base64
import hashlib
import io
import re
import tarfile
import urllib.request
from datetime import date
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from board import RepoConfig, _resolve_config  # noqa: E402

SCHEMA_VERSION = 2
DEFAULT_WORKFLOW = "release.yml"
NPM_PUBLICATION_FLOOR = (26, 11, 26)
# One manual bootstrap publish; all later releases require provenance.
BOOTSTRAP_UNATTESTED_TAG = "v26.11.26"


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
    not_applicable: bool = False


@dataclass
class VerifyResult:
    schema_version: int
    version: str
    expected_tag: str
    tag_exists: CheckResult
    workflow_run: CheckResult
    release_published: CheckResult
    npm_published: CheckResult
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
    npm_check: Optional[CheckResult] = None,
    *, npm_applicable: bool = False,
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
                  f"(has release automation completed yet?)",
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

    if npm_check is None:
        npm_check = CheckResult(False, "npm publication not checked") if npm_applicable else CheckResult(
            True, "not_applicable: no launcher publication required", not_applicable=True)
    ok = tag_check.ok and run_check.ok and release_check.ok and (npm_check.not_applicable or npm_check.ok)
    return VerifyResult(
        schema_version=SCHEMA_VERSION,
        version=norm_version,
        expected_tag=expected_tag,
        tag_exists=tag_check,
        workflow_run=run_check,
        release_published=release_check,
        npm_published=npm_check,
        ok=ok,
    )


def mapped_npm_version(tag: str) -> str:
    """Mirror launcher/lib/version.js; no runtime Node dependency."""
    match = re.fullmatch(r"v([1-9][0-9])\.([1-9][0-9]?)\.([1-9][0-9]?)(?:\.([1-9][0-9]*))?", tag)
    if not match:
        raise ValueError("unsupported calendar tag")
    y, m, d, n = (int(v or 0) for v in match.groups())
    if date(2000 + y, m, d) < date(2026, 11, 25) or n > 9007199254740991:
        raise ValueError("tag below launcher floor or release number too large")
    return f"{y}.{m * 100 + d}.{n}"


def _download(url: str, limit: int) -> bytes:
    if not url.startswith("https://registry.npmjs.org/"):
        raise ValueError("unexpected npm registry URL")
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("npm response too large")
    return data


def fetch_npm_published(expected_tag: str, repo_root: Path) -> CheckResult:
    """Read registry metadata, attestation and tarball; never extract files.

    Presence of provenance is checked here; cryptographic verification remains
    npm audit signatures' job. No subprocess executes downloaded code.
    """
    try:
        manifest = json.loads((repo_root / "launcher/package.json").read_text())
    except (OSError, ValueError):
        manifest = {}
    if not isinstance(manifest, dict) or manifest.get("name") != "@nexaduo/marc":
        return CheckResult(True, "not_applicable: repository has no matching launcher package", not_applicable=True)
    parts = re.fullmatch(r"v([0-9]+)\.([0-9]+)\.([0-9]+)(?:\.[0-9]+)?", expected_tag)
    if parts and tuple(map(int, parts.groups())) < NPM_PUBLICATION_FLOOR:
        return CheckResult(True, "not_applicable: tag predates npm publication floor", not_applicable=True)
    try:
        version = mapped_npm_version(expected_tag)
    except ValueError:
        return CheckResult(False, "invalid npm release tag")
    try:
        metadata = json.loads(_download("https://registry.npmjs.org/@nexaduo%2fmarc", 16 * 1024 * 1024))
        package = metadata.get("versions", {}).get(version)
        if not package:
            return CheckResult(False, f"npm version {version} is missing")
        if metadata.get("dist-tags", {}).get("latest") != version:
            return CheckResult(False, f"npm latest does not point at {version}")
        dist = package["dist"]
        if expected_tag != BOOTSTRAP_UNATTESTED_TAG:
            attestation_url = dist.get("attestations", {}).get("url")
            if not attestation_url:
                return CheckResult(False, "npm provenance attestation is missing")
            attestations = json.loads(_download(attestation_url, 4 * 1024 * 1024)).get("attestations", [])
            if not any(a.get("predicateType") == "https://slsa.dev/provenance/v1" and a.get("bundle") for a in attestations):
                return CheckResult(False, "npm provenance attestation is missing")
        blob = _download(dist["tarball"], 8 * 1024 * 1024)
        integrity = "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode("ascii")
        if dist.get("integrity") != integrity:
            return CheckResult(False, "npm tarball integrity mismatch")
        with tarfile.open(fileobj=io.BytesIO(blob), mode="r:gz") as archive:
            members = [m for m in archive.getmembers() if m.name == "package/release.json"]
            if len(members) != 1 or not members[0].isfile() or members[0].size > 4096:
                return CheckResult(False, "npm release.json missing, duplicated or unsafe")
            release = json.load(archive.extractfile(members[0]))
        expected_sha = _run(["git", "-C", str(repo_root), "rev-parse", f"{expected_tag}^{{commit}}"]).strip()
        if not re.fullmatch(r"[a-f0-9]{40}", expected_sha) or release.get("sha") != expected_sha:
            return CheckResult(False, "npm release.json SHA does not match peeled tag commit")
        if release.get("tag") != expected_tag or release.get("npm") != version or package.get("version") != version:
            return CheckResult(False, "npm release tag/version metadata mismatch")
        provenance = "bootstrap provenance exception" if expected_tag == BOOTSTRAP_UNATTESTED_TAG else "attested"
        return CheckResult(True, f"npm {version} is latest, {provenance}, and matches {expected_sha}")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(False, f"npm publication unreadable: {exc}")


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


def fetch_tag_commit(repo: str, expected_tag: str) -> Optional[str]:
    """Peel annotated tags via GitHub; consumer verification needs no clone."""
    try:
        obj = json.loads(_run(["gh", "api", f"repos/{repo}/git/refs/tags/{expected_tag}"]))["object"]
        for _ in range(8):
            sha = obj["sha"]
            if not re.fullmatch(r"[a-f0-9]{40}", sha):
                return None
            if obj["type"] == "commit":
                return sha
            if obj["type"] != "tag":
                return None
            obj = json.loads(_run(["gh", "api", f"repos/{repo}/git/tags/{sha}"]))["object"]
    except Exception:  # noqa: BLE001 — legacy tag-branch lookup can still work
        return None
    return None


def _dispatch_matches_tag(repo: str, run: dict, expected_tag: str) -> bool:
    """A dispatch checks out main, but can release an older existing tag.

    Only accept later-head dispatches whose immutable main manifest names this
    version. Never credit an unrelated release's successful dispatch.
    """
    sha = run.get("headSha", "")
    if not re.fullmatch(r"[a-f0-9]{40}", sha):
        return False
    try:
        path = "harnesses/claude-code/marc/.claude-plugin/plugin.json"
        response = json.loads(_run(["gh", "api", f"repos/{repo}/contents/{path}?ref={sha}"]))
        manifest = json.loads(base64.b64decode(response["content"], validate=False))
        return "v" + manifest["version"] == expected_tag
    except Exception:  # noqa: BLE001 — consumers may have no such manifest
        return False


def fetch_workflow_runs(repo: Optional[str], workflow: str, expected_tag: str) -> list[WorkflowRun]:
    def lookup(*filters):
        cmd = ["gh", "run", "list", "--workflow", workflow, *filters,
               "--json", "status,conclusion,url,createdAt,headSha,headBranch,event", "--limit", "100"]
        if repo:
            cmd += ["--repo", repo]
        return json.loads(_run(cmd))

    candidates = []
    sha = fetch_tag_commit(repo, expected_tag) if repo else None
    try:
        if sha:
            candidates = [r for r in lookup("--commit", sha, "--branch", "main")
                          if r.get("headSha") == sha and r.get("headBranch") == "main"
                          and r.get("event") in ("push", "workflow_dispatch")]
            # On dispatch, GITHUB_SHA is main's current head, while plan may target
            # the existing tag's older commit. Include such bootstrap retries too.
            # Bound manifest lookups even for dispatch-heavy consumer workflows.
            # gh returns newest first; only the newest matching dispatch matters.
            for run in lookup("--branch", "main", "--event", "workflow_dispatch")[:5]:
                if (run.get("headBranch") == "main" and run.get("event") == "workflow_dispatch"
                        and (run.get("headSha") == sha or _dispatch_matches_tag(repo, run, expected_tag))):
                    candidates.append(run)
                    break
    except Exception:  # noqa: BLE001 — fall back for consumer workflow APIs
        candidates = []
    if not candidates:
        # Consumers can retain their own tag-triggered workflows (any branch
        # naming scheme); no launcher or registry dependency is introduced.
        candidates = lookup("--branch", expected_tag)
    candidates.sort(key=lambda r: r.get("createdAt") or "", reverse=True)
    return [WorkflowRun(status=r.get("status"), conclusion=r.get("conclusion"),
                        url=r.get("url"), created_at=r.get("createdAt")) for r in candidates]


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
        f"  [{'N/A' if result.npm_published.not_applicable else mark(result.npm_published.ok)}] npm_published     — {result.npm_published.notes}",
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
    parser.add_argument("--repo", default=None, help="OWNER/REPO override (default: resolved like board.py)")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW,
                         help=f"release workflow file name (default: {DEFAULT_WORKFLOW})")
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

    npm_check = fetch_npm_published(expected_tag, repo_root)
    result = verify_release(version, tag_exists, runs, releases, npm_check)

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_human(result))

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
