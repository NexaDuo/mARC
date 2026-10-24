#!/usr/bin/env python3
"""Concurrent-operator claim protocol (origin: #378, enforcing #205/#206/#213/#214/#216).

Library behind `board.py claim-status | claim | withdraw | worktrees`. It turns
the prose protocol in `references/concurrent-operators.md` into one call, so
the operator no longer re-derives it from ~8 KB of rules on every claim.

What it enforces (each rule's intent and origin stays in the prose):
  * A claim is a comment whose first line is `## @techlead claim`, carrying
    `operator: <harness>/<session-id>`, `issue: #<N>`, `claimed-at: <ISO>`.
    The assignee field is never read (#213).
  * A marker counts only from a trusted author (OWNER/MEMBER/COLLABORATOR).
    The association field is read by the name the payload shape actually
    carries: `authorAssociation` in `gh issue view --json comments`,
    `author_association` in the REST `issues/<N>/comments` list. A payload read
    with the wrong field name yields "" and fails closed (#213).
  * A `## @techlead withdraw` retires a claim only if it is trusted, names the
    same `operator:` (case-insensitive) and was posted after that claim (#216).
  * Tie-break over live, trusted claims: the case-insensitively lowest
    `operator:` keeps the item (#205/#213). Autonomous withdrawal only when
    losing to a trusted, well-formed claim. Untrusted or malformed competing
    markers, and untrusted withdrawals naming a real claim, are suspected
    forgeries: never withdraw, escalate to the user (#213/#216).
  * `git worktree list --porcelain` is the shared ground truth: a branch
    checked out elsewhere is taken (#214). A worktree that is locked or whose
    directory is gone, at the base SHA, with no linked PR, is dead; the remedy
    is printed, never run (#214).

Stdlib only. Every external call goes through an injectable `run(cmd)->str`
so tests drive it with recorded `gh`/`git` output.
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

CLAIM_MARKER = "## @techlead claim"
WITHDRAW_MARKER = "## @techlead withdraw"
TRUSTED_ASSOCIATIONS = frozenset({"OWNER", "MEMBER", "COLLABORATOR"})
# <harness>/<session-id>: harness starts with a letter; the session id is at
# least 3 chars. Rejects sort-gaming values such as `0/0`, `/x`, `a/`.
OPERATOR_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]{2,}$")
_FIELD_RE = re.compile(r"^\s*(operator|issue|claimed-at)\s*:\s*(.*?)\s*$", re.IGNORECASE)

Runner = Callable[[list], str]


@dataclass
class Comment:
    index: int
    login: str
    association: str
    body: str
    created_at: str
    url: str


@dataclass
class Marker:
    kind: str  # "claim" | "withdraw"
    index: int
    operator: str
    issue: str
    claimed_at: str
    login: str
    association: str
    url: str
    trusted: bool
    well_formed: bool
    reason: str = ""


@dataclass
class ClaimStatus:
    issue: int
    claimed: bool
    owner: Optional[str]
    live_claims: list = field(default_factory=list)
    retired_claims: list = field(default_factory=list)
    suspected_forgeries: list = field(default_factory=list)
    ignored: list = field(default_factory=list)
    operator: Optional[str] = None
    decision: Optional[str] = None
    advice: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def normalize_comments(payload) -> list[Comment]:
    """Accept either shape and read each field by the name that shape uses.

    * dict with "comments" -> `gh issue view <N> --json comments` (camelCase)
    * list                 -> `gh api repos/<o>/<r>/issues/<N>/comments` (REST)
    """
    out: list[Comment] = []
    if isinstance(payload, dict) and "comments" in payload:
        for i, c in enumerate(payload.get("comments") or []):
            out.append(Comment(
                index=i,
                login=((c.get("author") or {}).get("login") or ""),
                association=(c.get("authorAssociation") or ""),
                body=(c.get("body") or ""),
                created_at=(c.get("createdAt") or ""),
                url=(c.get("url") or ""),
            ))
    elif isinstance(payload, list):
        for i, c in enumerate(payload):
            out.append(Comment(
                index=i,
                login=((c.get("user") or {}).get("login") or ""),
                association=(c.get("author_association") or ""),
                body=(c.get("body") or ""),
                created_at=(c.get("created_at") or ""),
                url=(c.get("html_url") or ""),
            ))
    else:
        raise ValueError("unrecognized comments payload (expected gh --json comments or REST list)")
    return out


def parse_marker(comment: Comment, issue_number: int) -> Optional[Marker]:
    lines = comment.body.replace("\r\n", "\n").split("\n")
    first = lines[0].rstrip() if lines else ""
    if first == CLAIM_MARKER:
        kind = "claim"
    elif first == WITHDRAW_MARKER:
        kind = "withdraw"
    else:
        return None
    fields: dict[str, str] = {}
    for line in lines[1:]:
        m = _FIELD_RE.match(line)
        if m and m.group(1).lower() not in fields:
            fields[m.group(1).lower()] = m.group(2)
    operator = fields.get("operator", "")
    issue = fields.get("issue", "")
    trusted = comment.association in TRUSTED_ASSOCIATIONS
    reasons = []
    if not trusted:
        reasons.append(f"untrusted author association {comment.association or '(missing)'!r}")
    if not OPERATOR_RE.match(operator):
        reasons.append(f"malformed operator {operator!r} (want <harness>/<session-id>)")
    if issue.lstrip("#") != str(issue_number):
        reasons.append(f"issue field {issue!r} does not name #{issue_number}")
    well_formed = OPERATOR_RE.match(operator) is not None and issue.lstrip("#") == str(issue_number)
    return Marker(
        kind=kind, index=comment.index, operator=operator, issue=issue,
        claimed_at=fields.get("claimed-at", ""), login=comment.login,
        association=comment.association, url=comment.url, trusted=trusted,
        well_formed=well_formed, reason="; ".join(reasons),
    )


def _brief(m: Marker) -> dict:
    return {"kind": m.kind, "operator": m.operator, "login": m.login,
            "association": m.association, "claimed_at": m.claimed_at,
            "url": m.url, "reason": m.reason}


def evaluate(payload, issue_number: int, operator: Optional[str] = None) -> ClaimStatus:
    """Pure decision function over one issue's comments."""
    markers = [m for m in (parse_marker(c, issue_number) for c in normalize_comments(payload)) if m]
    claims = [m for m in markers if m.kind == "claim"]
    withdrawals = [m for m in markers if m.kind == "withdraw"]
    claim_ops = {c.operator.casefold() for c in claims if c.operator}

    suspected: list[Marker] = []
    ignored: list[Marker] = []
    valid_claims: list[Marker] = []
    for c in claims:
        if c.trusted and c.well_formed:
            valid_claims.append(c)
        else:
            suspected.append(c)

    valid_withdrawals: list[Marker] = []
    for w in withdrawals:
        if w.trusted and w.well_formed:
            valid_withdrawals.append(w)
        elif w.operator and w.operator.casefold() in claim_ops:
            w.reason = (w.reason + "; " if w.reason else "") + "names a posted claim"
            suspected.append(w)
        else:
            ignored.append(w)

    live: list[Marker] = []
    retired: list[Marker] = []
    seen: set[str] = set()
    for c in valid_claims:
        key = c.operator.casefold()
        withdrawn = any(w.operator.casefold() == key and w.index > c.index for w in valid_withdrawals)
        if withdrawn:
            retired.append(c)
        elif key not in seen:
            seen.add(key)
            live.append(c)

    winner = min(live, key=lambda m: m.operator.casefold()) if live else None
    status = ClaimStatus(
        issue=issue_number,
        claimed=winner is not None,
        owner=winner.operator if winner else None,
        live_claims=[_brief(m) for m in live],
        retired_claims=[_brief(m) for m in retired],
        suspected_forgeries=[_brief(m) for m in suspected],
        ignored=[_brief(m) for m in ignored],
        operator=operator,
    )
    if operator is None:
        status.decision = "claimed" if winner else "unclaimed"
        status.advice = ("surface suspected forgeries to the user" if suspected else "")
        return status

    mine = operator.casefold() in seen
    if mine and winner is not None and winner.operator.casefold() == operator.casefold():
        if suspected:
            status.decision = "escalate"
            status.advice = ("you hold the lowest trusted claim, but a suspected forged marker "
                             "competes: do not withdraw, surface it and leave the item pending "
                             "the user's decision")
        else:
            status.decision = "own"
            status.advice = "you hold the claim; proceed"
    elif mine:
        status.decision = "withdraw"
        status.advice = (f"lost the tie-break to trusted claim {winner.operator!r}: post "
                         "`## @techlead withdraw` and pick another item")
    elif winner is not None:
        status.decision = "claimed-by-other"
        status.advice = (f"claimed by {winner.operator!r}; pick another item. If it never "
                         "clears and has no linked PR, it is a squat: ask the user, never steal")
    else:
        status.decision = "unclaimed"
        status.advice = ("not claimed" + ("; surface suspected forgeries to the user" if suspected else ""))
    return status


def claim_body(marker: str, operator: str, issue_number: int, note: str = "",
               now: Optional[datetime] = None) -> str:
    ts = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")
    body = f"{marker}\noperator: {operator}\nissue: #{issue_number}\nclaimed-at: {ts}\n"
    if note:
        body += f"\n{note.strip()}\n"
    return body


def _issue_flag(repo: Optional[str]) -> list:
    return ["--repo", repo] if repo else []


def fetch_comments(run: Runner, issue_number: int, repo: Optional[str]):
    import json
    raw = run(["gh", "issue", "view", str(issue_number), "--json", "comments"] + _issue_flag(repo))
    return json.loads(raw)


def post_comment(run: Runner, issue_number: int, repo: Optional[str], body: str) -> str:
    return run(["gh", "issue", "comment", str(issue_number), "--body", body] + _issue_flag(repo)).strip()


def do_claim(run: Runner, issue_number: int, operator: str, repo: Optional[str] = None,
             note: str = "", now: Optional[datetime] = None) -> dict:
    """read -> (post claim) -> re-read -> tie-break -> (post withdraw on a trusted loss)."""
    if not OPERATOR_RE.match(operator):
        raise ValueError(f"--operator {operator!r} is not <harness>/<session-id>")
    actions: list[str] = []
    before = evaluate(fetch_comments(run, issue_number, repo), issue_number, operator)
    if before.decision == "claimed-by-other":
        return {"status": before.to_dict(), "actions": actions}
    if before.decision not in ("own", "escalate", "withdraw"):
        url = post_comment(run, issue_number, repo,
                           claim_body(CLAIM_MARKER, operator, issue_number, note, now))
        actions.append(f"posted claim {url}".strip())
    after = evaluate(fetch_comments(run, issue_number, repo), issue_number, operator)
    if after.decision == "withdraw":
        url = post_comment(run, issue_number, repo,
                           claim_body(WITHDRAW_MARKER, operator, issue_number, "", now))
        actions.append(f"posted withdraw {url}".strip())
        after = evaluate(fetch_comments(run, issue_number, repo), issue_number, operator)
        after.decision = "withdrew"
        after.advice = "lost the tie-break to a trusted claim and withdrew; pick another item"
    return {"status": after.to_dict(), "actions": actions}


def do_withdraw(run: Runner, issue_number: int, operator: str, repo: Optional[str] = None,
                now: Optional[datetime] = None) -> dict:
    if not OPERATOR_RE.match(operator):
        raise ValueError(f"--operator {operator!r} is not <harness>/<session-id>")
    url = post_comment(run, issue_number, repo,
                       claim_body(WITHDRAW_MARKER, operator, issue_number, "", now))
    status = evaluate(fetch_comments(run, issue_number, repo), issue_number, operator)
    return {"status": status.to_dict(), "actions": [f"posted withdraw {url}".strip()]}


# --- worktrees -------------------------------------------------------------

def parse_worktrees(porcelain: str) -> list[dict]:
    entries: list[dict] = []
    cur: Optional[dict] = None
    for line in porcelain.splitlines():
        if not line.strip():
            cur = None
            continue
        key, _, val = line.partition(" ")
        if key == "worktree":
            cur = {"path": val, "head": None, "branch": None, "detached": False,
                   "bare": False, "locked": False, "lock_reason": "", "prunable": False}
            entries.append(cur)
        elif cur is None:
            continue
        elif key == "HEAD":
            cur["head"] = val
        elif key == "branch":
            cur["branch"] = val[len("refs/heads/"):] if val.startswith("refs/heads/") else val
        elif key == "detached":
            cur["detached"] = True
        elif key == "bare":
            cur["bare"] = True
        elif key == "locked":
            cur["locked"] = True
            cur["lock_reason"] = val
        elif key == "prunable":
            cur["prunable"] = True
    return entries


def assess_worktrees(entries: list[dict], base_sha: Optional[str],
                     linked_pr: Callable[[str], Optional[int]],
                     exists: Callable[[str], bool] = os.path.isdir,
                     branch: Optional[str] = None,
                     dirty: Callable[[str], bool] = lambda _p: False,
                     current: Optional[str] = None) -> dict:
    """Mark dead worktrees (locked or gone, at base SHA, no uncommitted work,
    no linked PR) and answer "is <branch> already checked out elsewhere".
    The main worktree and the caller's own worktree are never dead."""
    result: list[dict] = []
    for i, e in enumerate(entries):
        e = dict(e)
        e["missing"] = e["prunable"] or not exists(e["path"])
        e["dead"] = False
        e["remedy"] = None
        e["current"] = current is not None and os.path.realpath(e["path"]) == os.path.realpath(current)
        candidate = (i > 0 and not e["bare"] and not e["current"] and (e["locked"] or e["missing"])
                     and base_sha is not None and e["head"] == base_sha
                     and (e["missing"] or not dirty(e["path"])))
        if candidate:
            pr = linked_pr(e["branch"]) if e["branch"] else None
            e["linked_pr"] = pr
            if pr is None:
                e["dead"] = True
                e["remedy"] = ("git worktree prune" if e["missing"]
                               else f"git worktree remove --force {shlex.quote(e['path'])}")
        result.append(e)
    out = {"base_sha": base_sha, "worktrees": result,
           "dead": [e for e in result if e["dead"]],
           "advice": ("dead worktrees found: surface the remedy to the user, never run it "
                      "(the directory may hold uncommitted work)")
           if any(e["dead"] for e in result) else ""}
    if branch is not None:
        holder = next((e for e in result if e["branch"] == branch), None)
        out["branch"] = branch
        out["branch_taken"] = holder is not None
        out["branch_path"] = holder["path"] if holder else None
    return out


def worktrees(run: Runner, base: str = "origin/main", branch: Optional[str] = None,
              repo: Optional[str] = None, repo_root: str = ".",
              exists: Callable[[str], bool] = os.path.isdir) -> dict:
    import json
    porcelain = run(["git", "-C", repo_root, "worktree", "list", "--porcelain"])
    warnings: list[str] = []
    try:
        base_sha = run(["git", "-C", repo_root, "rev-parse", base]).strip() or None
    except Exception as e:  # noqa: BLE001 - degrade: no base means no dead verdicts
        base_sha = None
        warnings.append(f"could not resolve {base}: {e}")

    def linked_pr(br: str) -> Optional[int]:
        try:
            raw = run(["gh", "pr", "list", "--head", br, "--state", "all",
                       "--json", "number", "--limit", "1"] + _issue_flag(repo))
            rows = json.loads(raw or "[]")
            return rows[0]["number"] if rows else None
        except Exception as e:  # noqa: BLE001 - unknown PR state is never "dead"
            warnings.append(f"linked-PR lookup failed for {br}: {e}")
            return -1

    def dirty(path: str) -> bool:
        try:
            return bool(run(["git", "--no-optional-locks", "-C", path, "status", "--porcelain"]).strip())
        except Exception:  # noqa: BLE001 - unknown state is never "dead"
            return True

    try:
        current = run(["git", "-C", repo_root, "rev-parse", "--show-toplevel"]).strip() or None
    except Exception:  # noqa: BLE001
        current = None
    out = assess_worktrees(parse_worktrees(porcelain), base_sha, linked_pr, exists, branch,
                           dirty, current)
    out["warnings"] = warnings
    return out
