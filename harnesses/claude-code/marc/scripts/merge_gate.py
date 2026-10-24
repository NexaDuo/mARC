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
  * The verdict is the line-anchored `verdict: PASS|ADVISE|BLOCK` field
    (markdown emphasis tolerated), ignoring fenced code and HTML comments.
    BLOCK, no verdict, or two distinct values (ambiguous) blocks the merge.
    `reviewed-sha:` must be the full 40-char HEAD SHA.
  * A `reviewer:` line that is present but differs from the dispatched value
    (`--expect-reviewer`) is not counted (#216).
  * Unresolved, non-outdated review threads (inline bot/human findings) block:
    "CI green" is not permission to advance (#139).
  * `reviewDecision == REVIEW_REQUIRED` is reported early as a blocker that needs
    a named human ask; an empty `reviewDecision` is expected for a PR whose
    author cannot self-approve and is not a blocker (#105/#133).

Exit 0 does NOT cover CI checks or `mergeStateStatus` (reported, not
adjudicated): confirm CI separately.
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
# A verdict is a `verdict:` FIELD at the start of a line, optionally wrapped in
# markdown emphasis (`**Verdict: PASS**`, `Verdict: **PASS**`). Prose that
# merely mentions a verdict mid-line, blockquotes, fenced code and HTML
# comments never count (SEC-1, #382).
_VERDICT_RE = re.compile(r"(?im)^[ \t]*[*_]{0,3}[ \t]*verdict[ \t]*[*_]{0,3}[ \t]*:[ \t]*[*_`]{0,3}[ \t]*"
                         r"(PASS|ADVISE|BLOCK)\b")
_SHA_RE = re.compile(r"(?im)^[ \t]*reviewed-sha[ \t]*:[ \t]*`?([0-9a-f]{40})\b")
_REVIEWER_RE = re.compile(r"(?im)^[ \t]*reviewer[ \t]*:[ \t]*`?([^`\s]+)")
_FENCE_RE = re.compile(r"(?ms)^[ \t]*(```|~~~).*?(^[ \t]*\1[^\n]*$|\Z)")
_HTML_COMMENT_RE = re.compile(r"(?s)<!--.*?(-->|\Z)")
MAX_THREAD_PAGES = 20

THREADS_QUERY = (
    "query($owner:String!,$repo:String!,$n:Int!,$after:String){repository(owner:$owner,name:$repo)"
    "{pullRequest(number:$n){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor} "
    "nodes{id isResolved isOutdated path "
    "comments(first:1){nodes{author{login} authorAssociation commit{oid} url body}}}}}}}"
)


def strip_non_prose(body: str) -> str:
    """Drop fenced code blocks and HTML comments (an unclosed one runs to the end)."""
    return _FENCE_RE.sub("", _HTML_COMMENT_RE.sub("", body))

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


AMBIGUOUS = "AMBIGUOUS"


def parse_verdict(body: str) -> Optional[str]:
    """The single `verdict:` field value; AMBIGUOUS (fail closed) when the
    body carries more than one distinct value."""
    found = {v.upper() for v in _VERDICT_RE.findall(strip_non_prose(body))}
    if not found:
        return None
    return found.pop() if len(found) == 1 else AMBIGUOUS


def sha_matches(reviewed: Optional[str], head: str) -> bool:
    """Full 40-char equality: a short prefix can be ground by a PR author (SEC-2, #382)."""
    return bool(reviewed) and len(reviewed) == 40 and len(head) == 40 and reviewed.lower() == head.lower()


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
        prose = strip_non_prose(body)
        m = _SHA_RE.search(prose)
        info["reviewed_sha"] = m.group(1) if m else None
        r = _REVIEWER_RE.search(prose)
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
        elif v["verdict"] == AMBIGUOUS:
            blockers.append(f"@{k} review at HEAD carries conflicting `verdict:` lines (fail closed): {v['url']}")

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
    return pr_json, fetch_threads(run, owner, name, pr)


def fetch_threads(run: Runner, owner: str, name: str, pr: int) -> list:
    """All review threads, paginated (REV-3/SEC-5, #382). Raises (exit 2,
    never a pass) if the pages don't end within MAX_THREAD_PAGES."""
    threads: list = []
    after: Optional[str] = None
    for _ in range(MAX_THREAD_PAGES):
        cmd = ["gh", "api", "graphql", "-F", f"owner={owner}", "-F", f"repo={name}",
               "-F", f"n={pr}", "-f", f"query={THREADS_QUERY}"]
        if after:
            cmd += ["-f", f"after={after}"]
        gql = json.loads(run(cmd))
        conn = ((((gql.get("data") or {}).get("repository") or {}).get("pullRequest") or {})
                .get("reviewThreads"))
        if conn is None:
            raise RuntimeError(f"no reviewThreads in GraphQL response: {gql.get('errors')}")
        threads += conn.get("nodes") or []
        page = conn.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return threads
        after = page.get("endCursor")
        if not after:
            raise RuntimeError("reviewThreads has a next page but no endCursor")
    raise RuntimeError(f"more than {MAX_THREAD_PAGES * 100} review threads; refusing to adjudicate")


def render(res: dict) -> str:
    lines = [f"merge gate: {'PASS' if res['ok'] else 'BLOCKED'} at HEAD {res['head'][:12]} "
             f"(mergeStateStatus={res['merge_state_status']}; CI is not checked here)"]
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
