#!/usr/bin/env python3
"""Pre-merge gate adjudication (origin: #378, enforcing #125/#133/#139/#216).

One call replaces the prose procedure in `references/review-release.md`:

    python3 merge_gate.py --pr N [--expect-reviewer sec=H/ID] [--expect-reviewer rev=H/ID] [--json]

It reads `gh pr view N --json comments,headRefOid,reviewDecision,mergeStateStatus,state`
plus the PR's review threads (GraphQL `reviewThreads`) and decides:

  * `## marc:sec review` / `## marc:rev review` (and the legacy `@` forms) markers count only from a trusted
    author (`authorAssociation` OWNER/MEMBER/COLLABORATOR; missing/unknown fails
    closed) (#216).
  * The latest trusted marker per reviewer whose `reviewed-sha:` equals the
    current `headRefOid` is the verdict; an older SHA does not count (#125).
  * Strict line grammar (#378, @arch decision on #382); the body is read raw
    (CRLF/CR normalised, trailing blanks per line stripped; no fence or HTML
    comment stripping, so nothing can disagree with the rendered comment):
      line 1  exactly `## marc:sec review` / `## marc:rev review`, or the legacy
              `## @sec review` / `## @rev review` (dual-accept, #419; the
              mention-free form avoids pinging real accounts). Matching is
              case-sensitive, like the legacy form; a wrong-case heading is
              detected as a suspect and fails closed.
      line 2  exactly `reviewer: <id>` (required; equal to --expect-reviewer)
      line 3  exactly `reviewed-sha: <40 lowercase hex>`
      result  exactly one line may lead with a `verdict` label and it must be
              `verdict: PASS|ADVISE|BLOCK` (lowercase label, uppercase value).
              Zero, two, or a differently shaped one is MALFORMED.
  * Allowlist: a kind passes only when its counted result is PASS or ADVISE.
    A marker with a valid header, HEAD sha and matching reviewer is the counted
    one for its kind (latest wins) whatever its result is.
  * An uncounted trusted marker is skippable only if (a) its header is valid and
    its full SHA is not HEAD, or (b) it is at HEAD with a different reviewer
    and a strict PASS/ADVISE. Everything else is a suspect and blocks until a
    later counted marker of the same kind supersedes it. The blocker prints the
    offending line; the same reviewer re-posts, the operator never edits it.
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
import html
import json
import re
import subprocess
import sys
import unicodedata
from typing import Callable, Optional

TRUSTED = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
KINDS = ("sec", "rev")
# Every pattern is ASCII-only: a Kelvin sign or dotless i must never read as a
# letter of the value (SEC-16, #382).
_REVIEWER_RE = re.compile(r"reviewer: (\S+)", re.ASCII)
_SHA_RE = re.compile(r"reviewed-sha: ([0-9a-f]{40})", re.ASCII)
_RESULT_RE = re.compile(r"verdict: (PASS|ADVISE|BLOCK)", re.ASCII)
# Any line that LEADS with a verdict label, in any decoration: it must be the
# one strict result line or the whole body is MALFORMED (SEC-17, REV-13).
_CANDIDATE_RE = re.compile(r"[^A-Za-z0-9\n]*verdict[^A-Za-z0-9\n]*[:\uff1a]", re.ASCII | re.IGNORECASE)
MAX_THREAD_PAGES = 20

THREADS_QUERY = (
    "query($owner:String!,$repo:String!,$n:Int!,$after:String){repository(owner:$owner,name:$repo)"
    "{pullRequest(number:$n){reviewThreads(first:100,after:$after){pageInfo{hasNextPage endCursor} "
    "nodes{id isResolved isOutdated path "
    "comments(first:1){nodes{author{login} authorAssociation commit{oid} url body}}}}}}}"
)


Runner = Callable[[list], str]


def _default_runner(cmd: list) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"command failed: {' '.join(cmd)}")
    return r.stdout


_ZERO_WIDTH_RE = re.compile("[\u200b-\u200d\u2060\ufeff\u00ad]")
_TAG_RE = re.compile(r"<!--|-->|<[^>]*>")
_DECOR_RE = re.compile(r"[`*_~\\\u200e\u200f]")
DETECT_LINES = 5  # a heading-like line within the first few non-blank lines is a marker


def _detect_lines(body: str) -> list:
    """First DETECT_LINES non-blank lines in a normalised, rendering-like form. Used
    ONLY to decide whether a trusted comment could be read as a marker; never to count."""
    t = html.unescape(unicodedata.normalize("NFKC", body))
    t = _ZERO_WIDTH_RE.sub("", unicodedata.normalize("NFKC", t))
    t = _DECOR_RE.sub("", _TAG_RE.sub(" ", t))
    out = []
    for ln in re.split(r"[\r\n\u2028\u2029\x0b\x0c]", t):
        ln = re.sub(r"\s+", " ", ln).strip().lower()
        if ln:
            out.append(ln.lstrip("#> -+=|").lstrip())
            if len(out) == DETECT_LINES:
                break
    return out


def headers_for(kind: str) -> tuple:
    """Accepted line-1 headers for a kind: the mention-free form first, then legacy."""
    return (f"## marc:{kind} review", f"## @{kind} review")


def marker_kind(body: str) -> Optional[str]:
    """Detection runs on a normalised form (NFKC, BOM/zero-width/NBSP, whitespace,
    HTML tags/comments/entities, emphasis/backticks/backslashes, any heading level or
    setext form) over the first few lines, so a trusted comment that renders like a
    marker heading is a suspect and not silently skipped (SEC-20/REV-18, #382). This
    is wider than @arch rule 2's exact prefix. It is a heuristic over what a reader
    sees: prose that only mentions a review mid-line is out of reach by design. The
    strict header grammar (classify) alone decides whether a marker COUNTS. The
    `marc` form is detected more loosely than it counts (any non-alphanumeric
    separators, optional `@`), so `marc sec review`, `marc-sec review` or
    `marc::sec review` are suspects; `marc security review` is not (#419)."""
    for ln in _detect_lines(body):
        for k in KINDS:
            if ln.startswith(f"@{k} review") or re.match(rf"marc[^a-z0-9]*@?{k}[^a-z0-9]*review", ln, re.ASCII):
                return k
    return None


def trusted_markers(comments: list) -> list:
    """Same selection as the old reference jq filter: marker prefix AND trusted association."""
    return [c for c in comments
            if marker_kind(c.get("body") or "") and (c.get("authorAssociation") or "") in TRUSTED]


MALFORMED = "MALFORMED"
OK_RESULTS = ("PASS", "ADVISE")


def _clip(line: str) -> str:
    return repr(line if len(line) <= 120 else line[:117] + "...")


def classify(body: str, kind: str) -> dict:
    """Parse one marker body against the strict grammar. Never raises; every
    shape that is not exactly right is reported in `header_error` / `problem`."""
    lines = [ln.rstrip(" \t") for ln in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    lines += [""] * (3 - len(lines))
    out: dict = {"reviewer": None, "reviewed_sha": None, "header_error": None,
                 "verdict": MALFORMED, "problem": ""}
    if lines[0] not in headers_for(kind):
        out["header_error"] = (f"line 1 is not exactly `## marc:{kind} review` "
                               f"(or legacy `## @{kind} review`): {_clip(lines[0])}")
    m = _REVIEWER_RE.fullmatch(lines[1])
    if m:
        out["reviewer"] = m.group(1)
    elif not out["header_error"]:
        out["header_error"] = f"line 2 is not exactly `reviewer: <id>`: {_clip(lines[1])}"
    m = _SHA_RE.fullmatch(lines[2])
    if m:
        out["reviewed_sha"] = m.group(1)
    elif not out["header_error"]:
        out["header_error"] = f"line 3 is not exactly `reviewed-sha: <40 hex>`: {_clip(lines[2])}"
    cands = [ln for ln in lines if _CANDIDATE_RE.match(ln)]
    if not cands:
        out["problem"] = "no `verdict: PASS|ADVISE|BLOCK` line"
    elif len(cands) > 1:
        out["problem"] = f"{len(cands)} lines lead with a verdict label (exactly one allowed), e.g. {_clip(cands[1])}"
    else:
        m = _RESULT_RE.fullmatch(cands[0])
        if m:
            out["verdict"] = m.group(1)
        else:
            out["problem"] = f"verdict line is not exactly `verdict: PASS|ADVISE|BLOCK`: {_clip(cands[0])}"
    return out


def parse_verdict(body: str) -> str:
    """PASS | ADVISE | BLOCK | MALFORMED for the result line alone (header ignored)."""
    return classify(body, "sec")["verdict"]


def sha_matches(reviewed: Optional[str], head: str) -> bool:
    """Full 40-char equality: a short prefix can be ground by a PR author (SEC-2, #382)."""
    return bool(reviewed) and len(reviewed) == 40 and len(head) == 40 and reviewed.lower() == head.lower()


def adjudicate(pr: dict, threads: list, expect_reviewer: Optional[dict] = None) -> dict:
    head = pr.get("headRefOid") or ""
    expect_reviewer = expect_reviewer or {}
    blockers: list[str] = []
    verdicts: dict = {}
    ignored: list[dict] = []
    suspect: dict = {}  # kind -> uncounted trusted markers that block until superseded

    for c in pr.get("comments") or []:
        body = c.get("body") or ""
        kind = marker_kind(body)
        if not kind:
            continue
        info = {"kind": kind, "url": c.get("url", ""), "login": (c.get("author") or {}).get("login", ""),
                "association": c.get("authorAssociation") or "", "created_at": c.get("createdAt", "")}
        info.update(classify(body, kind))
        if info["association"] not in TRUSTED:
            ignored.append({**info, "reason": "untrusted author association"})
            continue
        want = expect_reviewer.get(kind)
        if info["header_error"]:
            # Closed skip list (rule 7): an unreadable header is never skippable.
            ignored.append({**info, "reason": info["header_error"]})
            suspect.setdefault(kind, []).append(info)
            continue
        if not sha_matches(info["reviewed_sha"], head):
            ignored.append({**info, "reason": "reviewed-sha is not the current HEAD"})  # older round
            continue
        if want and info["reviewer"] != want:
            ignored.append({**info, "reason": f"reviewer {info['reviewer']!r} != dispatched {want!r}"})
            if info["verdict"] not in OK_RESULTS:
                suspect.setdefault(kind, []).append(info)
            continue
        verdicts[kind] = info  # counted whatever its result; the latest wins
        suspect.pop(kind, None)

    for k in KINDS:
        v = verdicts.get(k)
        for sus in suspect.get(k, []):
            why = sus["header_error"] or sus["problem"] or f"result {sus['verdict']} from reviewer {sus['reviewer']!r}"
            blockers.append(f"trusted @{k} marker cannot be counted ({why}); fail closed until the same "
                            f"reviewer re-posts a corrected comment (never edit it as the operator): {sus['url']}")
        if v is None:
            blockers.append(f"no trusted `## @{k} review` (or `## marc:{k} review`) at HEAD {head[:12]} (re-review at HEAD)")
        elif v["verdict"] not in OK_RESULTS:
            why = v["problem"] if v["verdict"] == MALFORMED else "reviewer BLOCK"
            blockers.append(f"@{k} counted result is {v['verdict']} ({why}); "
                            f"the same reviewer re-posts if it is a formatting slip: {v['url']}")

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
