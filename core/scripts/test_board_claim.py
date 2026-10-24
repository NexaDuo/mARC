#!/usr/bin/env python3
"""Self-test for the claim protocol in `board_claim.py` / `board.py claim*`
(origin: #378). Stdlib only; run directly:  python3 test_board_claim.py

Fixtures are recorded `gh` payload shapes, verified on 2026-10-24 against
NexaDuo/mARC#226 (the only claimed issue at the time):
  * `gh issue view 226 --json comments`  -> {"comments":[{author:{login},
    authorAssociation, body, createdAt, id, url, ...}]}   (camelCase)
  * `gh api repos/NexaDuo/mARC/issues/226/comments` -> [{user:{login},
    author_association, body, created_at, html_url, ...}] (snake_case)
and `git worktree list --porcelain` output from the same clone. Bodies of
synthetic comments reuse the recorded shape; logins are placeholders.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import board_claim as bc  # noqa: E402

# Recorded `gh issue view 226 --json comments` (body trimmed after the fields).
GH_VIEW_226 = json.loads(r'''{
  "comments": [
    {
      "id": "IC_kwDOTM3EE88AAAABRW798A",
      "author": {"login": "alexandre-machado"},
      "authorAssociation": "MEMBER",
      "body": "## @techlead claim\noperator: claude-code/ef7c6e1b\nissue: #226\nclaimed-at: 2026-08-29T02:47:30Z\n\nWorking in an isolated worktree on branch `cost/226-skill-diet`, cut from `origin/main` at 43198a3.",
      "createdAt": "2026-08-29T02:47:32Z",
      "includesCreatedEdit": false,
      "isMinimized": false,
      "minimizedReason": "",
      "reactionGroups": [],
      "url": "https://github.com/NexaDuo/mARC/issues/226#issuecomment-5459869168",
      "viewerDidAuthor": true
    }
  ]
}''')

# Recorded `gh api repos/NexaDuo/mARC/issues/226/comments` (one element).
REST_226 = json.loads(r'''[
  {
    "url": "https://api.github.com/repos/NexaDuo/mARC/issues/comments/5459869168",
    "html_url": "https://github.com/NexaDuo/mARC/issues/226#issuecomment-5459869168",
    "issue_url": "https://api.github.com/repos/NexaDuo/mARC/issues/226",
    "id": 5459869168,
    "node_id": "IC_kwDOTM3EE88AAAABRW798A",
    "user": {"login": "alexandre-machado", "type": "User"},
    "created_at": "2026-08-29T02:47:32Z",
    "updated_at": "2026-08-29T02:47:32Z",
    "body": "## @techlead claim\noperator: claude-code/ef7c6e1b\nissue: #226\nclaimed-at: 2026-08-29T02:47:30Z\n",
    "author_association": "MEMBER",
    "pin": null,
    "performed_via_github_app": null
  }
]''')

# Recorded `git worktree list --porcelain` shape (paths shortened).
PORCELAIN = """worktree /repo
HEAD 8e68d8ca957d0fe13fd2a9a5a96f3e06d9a65c60
branch refs/heads/main

worktree /repo-worktrees/agy-343
HEAD dc542d555e6b8f6b7e90388c89e5292891dd801d
branch refs/heads/fix/343-agy-hook-dispatcher

worktree /agents/codex/worktrees/9419/agent-afc5717fb58b6eecd
HEAD 2946113ddc5cfe46205ea6e83174db1266830eb4
detached

worktree /repo/.claude/worktrees/agent-a6403d16481a59d45
HEAD 8e68d8ca957d0fe13fd2a9a5a96f3e06d9a65c60
branch refs/heads/fix/349-sandbox-tests-outside
locked claude agent agent-a6403d16481a59d45 (pid 4284 start 5037131)

worktree /repo/.claude/worktrees/gone
HEAD 8e68d8ca957d0fe13fd2a9a5a96f3e06d9a65c60
branch refs/heads/feat/gone
prunable gitdir file points to non-existent location

worktree /repo/.claude/worktrees/me
HEAD 8e68d8ca957d0fe13fd2a9a5a96f3e06d9a65c60
branch refs/heads/cost/378-scriptify
locked claude agent me
"""
BASE = "8e68d8ca957d0fe13fd2a9a5a96f3e06d9a65c60"


def view_comment(body, assoc="MEMBER", login="op", idx=0):
    c = copy.deepcopy(GH_VIEW_226["comments"][0])
    c.update({"body": body, "authorAssociation": assoc, "author": {"login": login},
              "url": f"https://example.invalid/c{idx}"})
    return c


def claim(op, n=226, marker=bc.CLAIM_MARKER):
    return f"{marker}\noperator: {op}\nissue: #{n}\nclaimed-at: 2026-10-24T00:00:00Z\n"


def view(*comments):
    return {"comments": list(comments)}


class RecordedShapes(unittest.TestCase):
    def test_gh_view_camelcase(self):
        st = bc.evaluate(GH_VIEW_226, 226)
        self.assertTrue(st.claimed)
        self.assertEqual(st.owner, "claude-code/ef7c6e1b")

    def test_rest_snake_case(self):
        st = bc.evaluate(REST_226, 226)
        self.assertEqual(st.owner, "claude-code/ef7c6e1b")
        self.assertEqual(st.live_claims[0]["url"], REST_226[0]["html_url"])

    def test_wrong_field_name_fails_closed(self):
        # A REST payload re-keyed as if it were `gh issue view` loses the
        # association: it must read as untrusted, never as a claim.
        mangled = {"comments": [{"author": {"login": "x"}, "author_association": "MEMBER",
                                 "body": REST_226[0]["body"]}]}
        st = bc.evaluate(mangled, 226)
        self.assertFalse(st.claimed)
        self.assertEqual(len(st.suspected_forgeries), 1)

    def test_assignee_is_never_read(self):
        st = bc.evaluate({"comments": [], "assignees": [{"login": "someone"}]}, 226)
        self.assertFalse(st.claimed)
        self.assertEqual(st.decision, "unclaimed")


class Rules(unittest.TestCase):
    def test_untrusted_claims_are_suspected(self):
        for assoc in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", ""):
            st = bc.evaluate(view(view_comment(claim("claude-code/abc"), assoc)), 226)
            self.assertFalse(st.claimed, assoc)
            self.assertEqual(len(st.suspected_forgeries), 1, assoc)

    def test_tie_break_lowest_operator_case_insensitive(self):
        p = view(view_comment(claim("claude-code/sess-2"), idx=0),
                 view_comment(claim("Antigravity/sess-7"), idx=1))
        self.assertEqual(bc.evaluate(p, 226, "claude-code/sess-2").decision, "withdraw")
        self.assertEqual(bc.evaluate(p, 226, "Antigravity/sess-7").decision, "own")

    def test_forged_low_sorting_claim_never_forces_withdraw(self):
        p = view(view_comment(claim("claude-code/sess-2"), idx=0),
                 view_comment(claim("0/0"), "MEMBER", idx=1),
                 view_comment(claim("aaa/forged"), "NONE", idx=2))
        st = bc.evaluate(p, 226, "claude-code/sess-2")
        self.assertEqual(st.decision, "escalate")
        self.assertEqual(st.owner, "claude-code/sess-2")
        self.assertEqual(len(st.suspected_forgeries), 2)

    def test_wrong_issue_field_is_malformed(self):
        st = bc.evaluate(view(view_comment(claim("claude-code/abc", n=999))), 226)
        self.assertFalse(st.claimed)

    def test_trusted_matching_withdraw_after_claim_retires(self):
        p = view(view_comment(claim("claude-code/abc"), idx=0),
                 view_comment(claim("claude-code/ABC", marker=bc.WITHDRAW_MARKER), idx=1))
        st = bc.evaluate(p, 226)
        self.assertFalse(st.claimed)
        self.assertEqual(len(st.retired_claims), 1)

    def test_withdraw_before_claim_does_not_retire(self):
        p = view(view_comment(claim("claude-code/abc", marker=bc.WITHDRAW_MARKER), idx=0),
                 view_comment(claim("claude-code/abc"), idx=1))
        self.assertTrue(bc.evaluate(p, 226).claimed)

    def test_withdraw_other_operator_does_not_retire(self):
        p = view(view_comment(claim("claude-code/abc"), idx=0),
                 view_comment(claim("claude-code/xyz", marker=bc.WITHDRAW_MARKER), idx=1))
        self.assertEqual(bc.evaluate(p, 226).owner, "claude-code/abc")

    def test_forged_withdraw_is_suspected_not_retiring(self):
        p = view(view_comment(claim("claude-code/abc"), idx=0),
                 view_comment(claim("claude-code/abc", marker=bc.WITHDRAW_MARKER), "NONE", idx=1))
        st = bc.evaluate(p, 226)
        self.assertEqual(st.owner, "claude-code/abc")
        self.assertEqual(st.suspected_forgeries[0]["kind"], "withdraw")

    def test_other_holds_claim(self):
        st = bc.evaluate(GH_VIEW_226, 226, "codex/sess-9")
        self.assertEqual(st.decision, "claimed-by-other")

    def test_non_marker_comments_ignored(self):
        p = view(view_comment("Looks fine.\n## @techlead claim\noperator: a/bcd\nissue: #226"))
        self.assertFalse(bc.evaluate(p, 226).claimed)


class FakeGh:
    """Replays `gh issue view` from an in-memory thread; `gh issue comment` appends."""

    def __init__(self, comments, inject_after_post=None):
        self.comments = list(comments)
        self.inject = inject_after_post or []
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "view"]:
            return json.dumps({"comments": self.comments})
        if cmd[:3] == ["gh", "issue", "comment"]:
            body = cmd[cmd.index("--body") + 1]
            self.comments.append(view_comment(body, idx=len(self.comments)))
            self.comments.extend(self.inject)
            self.inject = []
            return f"https://example.invalid/c{len(self.comments)}\n"
        raise AssertionError(f"unexpected command {cmd}")


NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)


class Flows(unittest.TestCase):
    def test_claim_unclaimed(self):
        gh = FakeGh([])
        out = bc.do_claim(gh, 226, "claude-code/sess-2", "o/r", now=NOW)
        self.assertEqual(out["status"]["decision"], "own")
        posted = [c for c in gh.calls if c[2] == "comment"]
        self.assertEqual(len(posted), 1)
        self.assertIn("--repo", posted[0])
        self.assertTrue(posted[0][posted[0].index("--body") + 1].startswith(
            "## @techlead claim\noperator: claude-code/sess-2\nissue: #226\nclaimed-at: 2026-10-24T12:00:00Z"))

    def test_claim_race_lost_withdraws(self):
        racer = view_comment(claim("antigravity/sess-7"), idx=99)
        gh = FakeGh([], inject_after_post=[racer])
        out = bc.do_claim(gh, 226, "claude-code/sess-2", now=NOW)
        self.assertEqual(out["status"]["decision"], "withdrew")
        self.assertEqual(out["status"]["owner"], "antigravity/sess-7")
        bodies = [c[c.index("--body") + 1] for c in gh.calls if c[2] == "comment"]
        self.assertTrue(bodies[1].startswith(bc.WITHDRAW_MARKER))

    def test_claim_race_with_forgery_escalates(self):
        forged = view_comment(claim("0/0"), "NONE", idx=99)
        gh = FakeGh([], inject_after_post=[forged])
        out = bc.do_claim(gh, 226, "claude-code/sess-2", now=NOW)
        self.assertEqual(out["status"]["decision"], "escalate")
        self.assertEqual(len([c for c in gh.calls if c[2] == "comment"]), 1)

    def test_claim_taken_posts_nothing(self):
        gh = FakeGh(GH_VIEW_226["comments"])
        out = bc.do_claim(gh, 226, "codex/sess-9", now=NOW)
        self.assertEqual(out["status"]["decision"], "claimed-by-other")
        self.assertFalse([c for c in gh.calls if c[2] == "comment"])

    def test_bad_operator_rejected(self):
        with self.assertRaises(ValueError):
            bc.do_claim(FakeGh([]), 226, "0/0")


class CliExitCodes(unittest.TestCase):
    """REV-1 (#382): `board.py claim-status` / `claim` exit codes, end to end
    through `board.main()` with `gh` replaced by recorded payloads."""

    def run_cli(self, argv, gh):
        import board  # noqa: PLC0415
        import io
        import tempfile
        from contextlib import redirect_stderr, redirect_stdout

        def runner(cmd):
            if cmd[:3] == ["gh", "repo", "view"]:
                return "o/r\n"
            if isinstance(gh, Exception):
                raise gh
            return gh(cmd)

        saved_runner, saved_argv = board._default_runner, sys.argv
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as root:
            board._default_runner = runner
            sys.argv = ["board.py"] + argv + ["--repo-root", root, "--json"]
            try:
                with redirect_stdout(out), redirect_stderr(io.StringIO()):
                    rc = board.main()
            finally:
                board._default_runner, sys.argv = saved_runner, saved_argv
        return rc, out.getvalue()

    def status(self, comments, operator=None):
        argv = ["claim-status", "--issue", "226"] + (["--operator", operator] if operator else [])
        return self.run_cli(argv, FakeGh(comments))

    def test_claim_status_codes(self):
        mine = view_comment(claim("claude-code/sess-2"), idx=0)
        lower = view_comment(claim("antigravity/sess-7"), idx=1)
        forged = view_comment(claim("0/0"), "NONE", idx=2)
        cases = [
            ("own", [mine], "claude-code/sess-2", 0),
            ("unclaimed", [], "claude-code/sess-2", 0),
            ("claimed-by-other (recorded #226)", GH_VIEW_226["comments"], "codex/sess-9", 1),
            ("lost tie-break", [mine, lower], "claude-code/sess-2", 1),
            ("escalate: forgery competes with own claim", [mine, forged], "claude-code/sess-2", 3),
            ("unclaimed but forged marker present", [forged], "claude-code/sess-2", 3),
            ("no --operator, claimed (recorded #226)", GH_VIEW_226["comments"], None, 1),
            ("no --operator, unclaimed", [], None, 0),
        ]
        for label, comments, op, want in cases:
            rc, out = self.status(comments, op)
            self.assertEqual(rc, want, f"{label}: {out}")

    def test_claim_status_error_is_2(self):
        rc, _ = self.run_cli(["claim-status", "--issue", "226"], RuntimeError("gh: HTTP 502"))
        self.assertEqual(rc, 2)

    def test_claim_codes(self):
        rc, _ = self.run_cli(["claim", "--issue", "226", "--operator", "claude-code/sess-2"], FakeGh([]))
        self.assertEqual(rc, 0)
        racer = view_comment(claim("antigravity/sess-7"), idx=99)
        rc, out = self.run_cli(["claim", "--issue", "226", "--operator", "claude-code/sess-2"],
                               FakeGh([], inject_after_post=[racer]))
        self.assertEqual(rc, 1, out)
        self.assertEqual(json.loads(out)["status"]["decision"], "withdrew")
        rc, _ = self.run_cli(["claim", "--issue", "226", "--operator", "codex/sess-9"],
                             FakeGh(GH_VIEW_226["comments"]))
        self.assertEqual(rc, 1)
        forged = view_comment(claim("0/0"), "NONE", idx=99)
        rc, _ = self.run_cli(["claim", "--issue", "226", "--operator", "claude-code/sess-2"],
                             FakeGh([], inject_after_post=[forged]))
        self.assertEqual(rc, 3)


class Worktrees(unittest.TestCase):
    def assess(self, linked=None, exists=None, dirty=None, branch=None):
        entries = bc.parse_worktrees(PORCELAIN)
        return bc.assess_worktrees(
            entries, BASE, linked or (lambda b: None),
            exists or (lambda p: p != "/repo/.claude/worktrees/gone"),
            branch, dirty or (lambda p: False), current="/repo/.claude/worktrees/me")

    def test_parse(self):
        e = bc.parse_worktrees(PORCELAIN)
        self.assertEqual(len(e), 6)
        self.assertEqual(e[1]["branch"], "fix/343-agy-hook-dispatcher")
        self.assertTrue(e[2]["detached"])
        self.assertTrue(e[3]["locked"])
        self.assertTrue(e[4]["prunable"])

    def test_dead_detection_and_remedy(self):
        out = self.assess()
        dead = {d["path"]: d["remedy"] for d in out["dead"]}
        self.assertEqual(dead, {
            "/repo/.claude/worktrees/agent-a6403d16481a59d45":
                "git worktree remove --force /repo/.claude/worktrees/agent-a6403d16481a59d45",
            "/repo/.claude/worktrees/gone": "git worktree prune",
        })
        self.assertIn("never run it", out["advice"])

    def test_remedy_path_is_shell_quoted(self):
        # SEC-6 (#382)
        porcelain = PORCELAIN.replace("agent-a6403d16481a59d45", "a b;rm -rf x")
        entries = bc.parse_worktrees(porcelain)
        out = bc.assess_worktrees(entries, BASE, lambda b: None, lambda p: True)
        remedies = [d["remedy"] for d in out["dead"]]
        self.assertIn("git worktree remove --force '/repo/.claude/worktrees/a b;rm -rf x'", remedies)

    def test_dirty_check_takes_no_optional_locks(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            if "worktree" in cmd:
                return PORCELAIN
            if "rev-parse" in cmd:
                return BASE + "\n"
            if cmd[:3] == ["gh", "pr", "list"]:
                return "[]"
            return ""

        bc.worktrees(run, exists=lambda p: True)
        status_calls = [c for c in calls if "status" in c]
        self.assertTrue(status_calls)
        self.assertTrue(all("--no-optional-locks" in c for c in status_calls))

    def test_linked_pr_or_dirty_is_not_dead(self):
        out = self.assess(linked=lambda b: 42 if b == "feat/gone" else None,
                          dirty=lambda p: True)
        self.assertEqual(out["dead"], [])

    def test_branch_taken(self):
        out = self.assess(branch="fix/343-agy-hook-dispatcher")
        self.assertTrue(out["branch_taken"])
        self.assertEqual(out["branch_path"], "/repo-worktrees/agy-343")
        self.assertFalse(self.assess(branch="feat/new")["branch_taken"])

    def test_no_base_no_dead(self):
        out = bc.assess_worktrees(bc.parse_worktrees(PORCELAIN), None, lambda b: None)
        self.assertEqual(out["dead"], [])


if __name__ == "__main__":
    unittest.main(verbosity=1)
