#!/usr/bin/env python3
"""Pre-merge gate adjudication (origin: #378, enforcing #125/#133/#139/#216).

One call replaces the prose procedure in `references/review-release.md`:

    python3 merge_gate.py --pr N [--expect-reviewer sec=H/ID] [--expect-reviewer rev=H/ID] [--json]

It reads `gh pr view N --json comments,headRefOid,reviewDecision,mergeStateStatus,state`
plus the PR's review threads (GraphQL `reviewThreads`) and decides:

  * `## @sec review` and `## @rev review` markers count only from a trusted
    author (`authorAssociation` OWNER/MEMBER/COLLABORATOR; missing/unknown fails
    closed) (#216).
  * The latest trusted marker per reviewer whose `reviewed-sha:` equals the
    current `headRefOid` is the verdict; an older SHA does not count (#125).
  * The verdict is the last `verdict: PASS|ADVISE|BLOCK` occurrence in the body
    (bold/colon tolerant). BLOCK or no verdict blocks the merge.
  * A `reviewer:` line that is present but differs from the dispatched value
    (`--expect-reviewer`) is not counted (#216).
  * Unresolved, non-outdated review threads (inline bot/human findings) block:
    "CI green" is not permission to advance (#139).
  * `reviewDecision == REVIEW_REQUIRED` is reported early as a blocker that needs
    a named human ask; an empty `reviewDecision` is expected for a PR whose
    author cannot self-approve and is not a blocker (#105/#133).

Exit: 0 = gate passes, 1 = blocked (see `blockers`), 2 = could not read the PR.
The marker check filters forgeries from non-collaborators; it does not
authenticate which reviewer wrote a verdict (trust boundary, #216).

`--list-trusted` with `--comments-file PATH|-` prints the trusted verdict
markers of a recorded `gh pr view --json comments` payload (used by
`test_review_trust.sh`). Stdlib only; every `gh` call goes through an
injectable runner so tests drive it with recorded output.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from typing import Callable, Optional

TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
KINDS = ("sec", "rev")
_VERDICT_RE = re.compile(r"(?i)\bverdict\b[\s:*_`]*(PASS|ADVISE|BLOCK)\b")
_SHA_RE = re.compile(r"(?im)^\s*reviewed-sha\s*:\s*`?([0-9a-f]{7,40})`?\s*$")
_REVIEWER_RE = re.compile(r"(?im)^\s*reviewer\s*:\s*`?([^`\s]+)`?\s*$")

THREADS_QUERY = (
    "query($owner:String!,$repo:String!,$n:Int!){repository(owner:$owner,name:$repo)"
    "{pullRequest(number:$n){reviewThreads(first:100){nodes{id isResolved isOutdated path "
    "comments(first:1){nodes{author{login} authorAssociation commit{oid} url body}}}}}}}"
)

Runner = Callable[[list], str]


def _default_runner(cmd: list) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"command failed: {' '.join(cmd)}")
    return r.stdout


def marker_kind(body: str) -> Optional[str]:
    for k in KINDS:
        if body.startswith(f"## @{k} review"):
            return k
    return None


def trusted_markers(comments: list) -> list:
    """Same selection as the old reference jq filter: marker prefix AND trusted association."""
    return [c for c in comments
            if marker_kind(c.get("body") or "") and (c.get("authorAssociation") or "") in TRUSTED]


def parse_verdict(body: str) -> Optional[str]:
    found = _VERDICT_RE.findall(body)
    return found[-1].upper() if found else None


def sha_matches(reviewed: Optional[str], head: str) -> bool:
    return bool(reviewed) and len(reviewed) >= 7 and head.lower().startswith(reviewed.lower())


def adjudicate(pr: dict, threads: list, expect_reviewer: Optional[dict] = None) -> dict:
    head = pr.get("headRefOid") or ""
    expect_reviewer = expect_reviewer or {}
    blockers: list[str] = []
    verdicts: dict = {}
    ignored: list[dict] = []

    for c in pr.get("comments") or []:
        body = c.get("body") or ""
        kind = marker_kind(body)
        if not kind:
            continue
        info = {"kind": kind, "url": c.get("url", ""), "login": (c.get("author") or {}).get("login", ""),
                "association": c.get("authorAssociation") or "", "created_at": c.get("createdAt", "")}
        m = _SHA_RE.search(body)
        info["reviewed_sha"] = m.group(1) if m else None
        r = _REVIEWER_RE.search(body)
        info["reviewer"] = r.group(1) if r else None
        info["verdict"] = parse_verdict(body)
        if info["association"] not in TRUSTED:
            ignored.append({**info, "reason": "untrusted author association"})
            continue
        if not sha_matches(info["reviewed_sha"], head):
            ignored.append({**info, "reason": "reviewed-sha is not the current HEAD"})
            continue
        want = expect_reviewer.get(kind)
        if want and info["reviewer"] and info["reviewer"] != want:
            ignored.append({**info, "reason": f"reviewer {info['reviewer']!r} != dispatched {want!r}"})
            continue
        verdicts[kind] = info  # comments are chronological; the latest wins

    for k in KINDS:
        v = verdicts.get(k)
        if v is None:
            blockers.append(f"no trusted `## @{k} review` at HEAD {head[:12]} (re-review at HEAD)")
        elif v["verdict"] is None:
            blockers.append(f"@{k} review at HEAD has no verdict line: {v['url']}")
        elif v["verdict"] == "BLOCK":
            blockers.append(f"@{k} verdict is BLOCK: {v['url']}")

    open_threads = []
    stale_threads = []
    for t in threads:
        if t.get("isResolved"):
            continue
        first = ((t.get("comments") or {}).get("nodes") or [{}])[0]
        row = {"id": t.get("id"), "path": t.get("path"), "url": first.get("url", ""),
               "author": (first.get("author") or {}).get("login", ""),
               "at_head": ((first.get("commit") or {}).get("oid") == head)}
        (stale_threads if t.get("isOutdated") else open_threads).append(row)
    if open_threads:
        blockers.append(f"{len(open_threads)} unresolved review thread(s): verify, reply citing the "
                        "fixing commit (or a won't-fix justification), then resolve")

    decision = pr.get("reviewDecision") or ""
    if decision == "REVIEW_REQUIRED":
        blockers.append("branch protection needs a non-author approval (REVIEW_REQUIRED): name a human "
                        "reviewer (`gh pr edit N --add-reviewer <user>`) and set the item to Blocked; "
                        "`--admin` only with explicit per-PR human authorization")
    if decision == "CHANGES_REQUESTED":
        blockers.append("a reviewer requested changes (reviewDecision CHANGES_REQUESTED)")

    return {
        "ok": not blockers,
        "head": head,
        "state": pr.get("state"),
        "review_decision": decision,
        "merge_state_status": pr.get("mergeStateStatus"),
        "verdicts": verdicts,
        "ignored_markers": ignored,
        "unresolved_threads": open_threads,
        "outdated_unresolved_threads": stale_threads,
        "blockers": blockers,
    }


def fetch(run: Runner, pr: int, repo: Optional[str]) -> tuple[dict, list]:
    flag = ["--repo", repo] if repo else []
    pr_json = json.loads(run(["gh", "pr", "view", str(pr), "--json",
                              "comments,headRefOid,reviewDecision,mergeStateStatus,state"] + flag))
    if not repo:
        repo = run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]).strip()
    owner, name = repo.split("/", 1)
    gql = json.loads(run(["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"repo={name}",
                          "-F", f"n={pr}", "-f", f"query={THREADS_QUERY}"]))
    threads = (((gql.get("data") or {}).get("repository") or {}).get("pullRequest") or {}) \
        .get("reviewThreads", {}).get("nodes") or []
    return pr_json, threads


def render(res: dict) -> str:
    lines = [f"merge gate: {'PASS' if res['ok'] else 'BLOCKED'} at HEAD {res['head'][:12]}"]
    for k in KINDS:
        v = res["verdicts"].get(k)
        lines.append(f"  @{k}: " + (f"{v['verdict']} {v['url']}" if v else "missing at HEAD"))
    for b in res["blockers"]:
        lines.append(f"  ! {b}")
    for t in res["unresolved_threads"]:
        lines.append(f"  - thread {t['path']} by {t['author']} {t['url']}")
    for i in res["ignored_markers"]:
        lines.append(f"  (ignored @{i['kind']} by {i['login']}: {i['reason']})")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--pr", type=int)
    p.add_argument("--repo", default=None, help="owner/name (default: the checked-out repo)")
    p.add_argument("--expect-reviewer", action="append", default=[], metavar="KIND=VALUE",
                   help="dispatched reviewer id, e.g. sec=claude-code/sec-pr12-1")
    p.add_argument("--json", action="store_true")
    p.add_argument("--list-trusted", action="store_true")
    p.add_argument("--comments-file", default=None)
    a = p.parse_args(argv)

    if a.list_trusted:
        if a.comments_file in (None, "-"):
            payload = json.load(sys.stdin)
        else:
            with open(a.comments_file, encoding="utf-8") as fh:
                payload = json.load(fh)
        for c in trusted_markers(payload.get("comments") or []):
            print(json.dumps(c, sort_keys=True))
        return 0
    if a.pr is None:
        p.error("--pr is required")
    expect = {}
    for item in a.expect_reviewer:
        k, _, v = item.partition("=")
        if k not in KINDS or not v:
            p.error(f"--expect-reviewer {item!r}: want sec=VALUE or rev=VALUE")
        expect[k] = v
    try:
        pr_json, threads = fetch(_default_runner, a.pr, a.repo)
    except Exception as e:  # noqa: BLE001 - unreadable PR never passes the gate
        print(f"error: {e}", file=sys.stderr)
        return 2
    res = adjudicate(pr_json, threads, expect)
    print(json.dumps(res, indent=2, sort_keys=True) if a.json else render(res))
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
