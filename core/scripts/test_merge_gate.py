#!/usr/bin/env python3
"""Self-test for `merge_gate.py` (origin: #378). Stdlib only; run directly:
    python3 test_merge_gate.py

Fixtures are recorded `gh` payload shapes, verified on 2026-10-24:
  * `gh pr view 374 --json comments,headRefOid,reviewDecision,mergeStateStatus,state`
    (NexaDuo/mARC#374: a NONE bot comment, then @sec/@rev at an older SHA,
    then @sec PASS / @rev ADVISE at the final head). Bodies are trimmed to
    the marker, `reviewed-sha:` and verdict lines.
  * GraphQL `pullRequest.reviewThreads` for NexaDuo/mARC#353 (`id`,
    `isResolved`, `isOutdated`, `path`, first comment `author`,
    `authorAssociation`, `commit.oid`, `url`).
"""
from __future__ import annotations

import copy
import io
import json
import os
import sys
import unittest
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import merge_gate as mg  # noqa: E402

HEAD = "2588cec2c505b2d49162b59333741afdf3ed2634"
OLD = "c10e9ea13cfb179c93da596ce0472826d7745afd"

PR_374 = json.loads(r'''{
  "headRefOid": "2588cec2c505b2d49162b59333741afdf3ed2634",
  "reviewDecision": "",
  "mergeStateStatus": "UNKNOWN",
  "state": "MERGED",
  "comments": [
    {"author": {"login": "qodo-code-review"}, "authorAssociation": "NONE",
     "createdAt": "2026-10-19T04:01:24Z", "id": "IC_kwDOTM3EE88AAAABdRY_Zg",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6259359590",
     "body": "<!-- qodo:billing-blocked -->\n\n**Qodo reviews are paused because your trial has ended.**"},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T04:09:16Z", "id": "IC_kwDOTM3EE88AAAABdReIFQ",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6259443733",
     "body": "## @sec review\n\nreviewed-sha: c10e9ea13cfb179c93da596ce0472826d7745afd\n\nScope: three-dot diff.\n\nVerdict: **PASS**"},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T04:12:11Z", "id": "IC_kwDOTM3EE88AAAABdRgFgg",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6259475842",
     "body": "## @rev review\n\nreviewed-sha: c10e9ea13cfb179c93da596ce0472826d7745afd\n\n**Verdict: PASS.** Findings 1 and 2 are non-blocking (ADVISE level)."},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T12:37:50Z", "id": "IC_kwDOTM3EE88AAAABdZC1mQ",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6267385241",
     "body": "## @sec review\n\nreviewed-sha: 2588cec2c505b2d49162b59333741afdf3ed2634\nscope: delta `c10e9ea..2588cec`\n\n**Verdict: PASS**"},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T12:37:50Z", "id": "IC_kwDOTM3EE88AAAABdZC1mw",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6267385243",
     "body": "## @rev review\n\nreviewed-sha: 2588cec2c505b2d49162b59333741afdf3ed2634\nscope: delta\n\n**Verdict: ADVISE** (low findings only; the medium finding from the previous round is resolved)"}
  ]
}''')

THREADS_353 = json.loads(r'''{"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
  {"id": "PRRT_kwDOTM3EE86sWL9A", "isResolved": true, "isOutdated": false,
   "path": "core/scripts/dispatch_agent.py",
   "comments": {"nodes": [{"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "commit": {"oid": "551216f3ace82aa0efd1279e2cd17398baedc6cb"},
     "url": "https://github.com/NexaDuo/mARC/pull/353#discussion_r4271640638",
     "body": "**Job store is never garbage-collected**"}]}}
]}}}}}''')["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]


SEC_382_BODY = """## @sec review
reviewer: unassigned
reviewed-sha: 5ed67134b7542258e4203633c78435f4c8ed7594

Three-dot diff on a fresh base (origin/main 8e68d8c). The claim logic is faithful to the old prose. The merge gate has one fail-open path.

**SEC-1 (high)** `core/scripts/merge_gate.py:46,79-81`. The parser takes the last "verdict" word followed by PASS/ADVISE/BLOCK anywhere in the body. A real BLOCK flips to a pass when later text says "the prior verdict PASS no longer holds", quotes a PASS line, or holds one inside an HTML comment. I reproduced all three: `ok: True`. Fix: match only a line-anchored `verdict:` field outside code fences and HTML comments, and treat more than one distinct value as a fail-closed ambiguity.

**SEC-2 (medium)** `merge_gate.py:47,84-85`. The old rule said reviewed-sha MUST equal HEAD. A 7-char prefix is 28 bits, which a PR author can grind (lucky_commit style) to reuse a short-SHA PASS on new code. `test_merge_gate.py:98` locks short SHAs in. Fix: require a full-length match.

<details><summary>Verified and limitations</summary>

Verified: the trust set, both association spellings and fail-closed on a missing value. gh errors exit 2.
</details>

verdict: BLOCK
"""

REV_382_BODY = """## @rev review
reviewer: unassigned
reviewed-sha: 5ed67134b7542258e4203633c78435f4c8ed7594

verdict: BLOCK

**REV-1 (high, verified)** `core/scripts/board.py:79`: `claim-status` exits 0 for `claimed-by-other` and for `withdraw` (lost tie-break).

<details><summary>Checks</summary>

- PR #379: `merge_gate.py` returns BLOCKED (rc=1) at 0426dad, with both 857a370 verdicts ignored as not-HEAD. Correct.
</details>
"""


def thread(resolved=False, outdated=False, oid=HEAD):
    t = copy.deepcopy(THREADS_353[0])
    t.update({"isResolved": resolved, "isOutdated": outdated})
    t["comments"]["nodes"][0]["commit"]["oid"] = oid
    return t


def pr(**kw):
    p = copy.deepcopy(PR_374)
    p.update(kw)
    return p


class Gate(unittest.TestCase):
    def test_recorded_pr_passes(self):
        res = mg.adjudicate(PR_374, THREADS_353)
        self.assertTrue(res["ok"], res["blockers"])
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "PASS")
        self.assertEqual(res["verdicts"]["rev"]["verdict"], "ADVISE")
        self.assertEqual(len(res["ignored_markers"]), 2)  # the c10e9ea round

    def test_new_commit_invalidates_verdicts(self):
        res = mg.adjudicate(pr(headRefOid="f" * 40), [])
        self.assertFalse(res["ok"])
        self.assertEqual(len([b for b in res["blockers"] if "re-review at HEAD" in b]), 2)

    def test_short_sha_never_counts(self):
        # SEC-2 (#382): a 7-char prefix is grindable; only the full HEAD SHA counts.
        p = pr()
        for short in ("2588cec", "2588cec2c505b2d4"):
            p["comments"][3]["body"] = f"## @sec review\nreviewed-sha: {short}\nVerdict: PASS"
            self.assertFalse(mg.adjudicate(p, [])["ok"], short)
        p["comments"][3]["body"] = f"## @sec review\nreviewed-sha: {HEAD.upper()}\nVerdict: PASS"
        self.assertTrue(mg.adjudicate(p, [])["ok"])  # same full SHA, case-insensitive

    def _sec(self, verdict_tail):
        p = pr()
        p["comments"][3]["body"] = f"## @sec review\nreviewer: unassigned\nreviewed-sha: {HEAD}\n\n" + verdict_tail
        return mg.adjudicate(p, [])

    def test_sec1_bypass_later_prose_mentions_pass(self):
        res = self._sec("verdict: BLOCK\n\nSEC-1: the prior verdict PASS no longer holds.")
        self.assertFalse(res["ok"])
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "BLOCK")

    def test_sec1_bypass_quoted_pass_line(self):
        for quoted in ("> Verdict: PASS", "Earlier round said `verdict: PASS`.",
                       "```\nverdict: PASS\n```", "~~~text\nVerdict: **PASS**\n~~~"):
            res = self._sec("verdict: BLOCK\n\n" + quoted)
            self.assertEqual(res["verdicts"]["sec"]["verdict"], "BLOCK", quoted)
            self.assertFalse(res["ok"], quoted)

    def test_sec1_bypass_html_comment(self):
        for tail in ("verdict: BLOCK\n<!-- verdict: PASS -->",
                     "verdict: BLOCK\n<!--\nVerdict: PASS\n-->",
                     "verdict: BLOCK\n<!-- unclosed\nverdict: PASS"):
            res = self._sec(tail)
            self.assertEqual(res["verdicts"]["sec"]["verdict"], "BLOCK", tail)

    def test_two_distinct_verdict_fields_fail_closed(self):
        res = self._sec("verdict: BLOCK\n\nfixed later\n\n**Verdict: PASS**")
        self.assertEqual(res["verdicts"]["sec"]["verdict"], mg.AMBIGUOUS)
        self.assertFalse(res["ok"])
        self.assertTrue(any("conflicting" in b for b in res["blockers"]))
        # Repeating the SAME value is not ambiguous.
        self.assertTrue(self._sec("Verdict: PASS\n\n**Verdict: PASS**")["ok"])

    def test_recorded_review_bodies_on_pr382_parse_as_block(self):
        # The BLOCK reviews posted on PR #382 at 5ed6713, verbatim except that
        # findings without verdict text (SEC-3..6, REV-2..4) are omitted:
        # @sec ends in `verdict: BLOCK` and its SEC-1 text says "the prior verdict
        # PASS no longer holds"; @rev puts `verdict: BLOCK` near the top.
        self.assertEqual(mg.parse_verdict(SEC_382_BODY), "BLOCK")
        self.assertEqual(mg.parse_verdict(REV_382_BODY), "BLOCK")
        self.assertEqual(mg._SHA_RE.search(SEC_382_BODY).group(1), "5ed67134b7542258e4203633c78435f4c8ed7594")

    def test_reviewer_first_token(self):
        # SEC-4 (#382): trailing text after the id must not hide a mismatch.
        p = pr()
        p["comments"][3]["body"] = (f"## @sec review\nreviewer: claude-code/sec-9 (round 2)\n"
                                    f"reviewed-sha: {HEAD}\nVerdict: PASS")
        res = mg.adjudicate(p, [], {"sec": "claude-code/sec-1"})
        self.assertFalse(res["ok"])
        self.assertEqual(res["ignored_markers"][-1]["reviewer"], "claude-code/sec-9")

    def test_block_verdict_blocks(self):
        p = pr()
        p["comments"][4]["body"] = f"## @rev review\nreviewed-sha: {HEAD}\nVerdict: **BLOCK**"
        res = mg.adjudicate(p, [])
        self.assertFalse(res["ok"])
        self.assertTrue(any("BLOCK" in b for b in res["blockers"]))

    def test_missing_verdict_blocks(self):
        p = pr()
        p["comments"][4]["body"] = f"## @rev review\nreviewed-sha: {HEAD}\nstill reading"
        self.assertFalse(mg.adjudicate(p, [])["ok"])

    def test_task_pipeline_verdict_line_shape(self):
        p = pr()
        p["comments"][3]["body"] = f"## @sec review\nreviewed-sha: {HEAD}\nverdict: PASS"
        self.assertEqual(mg.adjudicate(p, [])["verdicts"]["sec"]["verdict"], "PASS")

    def test_untrusted_or_missing_association_fails_closed(self):
        for assoc in ("NONE", "CONTRIBUTOR", "FIRST_TIME_CONTRIBUTOR", "", None):
            p = pr()
            for c in p["comments"][3:]:
                c["authorAssociation"] = assoc
            if assoc is None:
                for c in p["comments"][3:]:
                    del c["authorAssociation"]
            self.assertFalse(mg.adjudicate(p, [])["ok"], assoc)

    def test_expected_reviewer_mismatch_not_counted(self):
        p = pr()
        p["comments"][3]["body"] = f"## @sec review\nreviewer: claude-code/sec-pr374-9\nreviewed-sha: {HEAD}\nVerdict: PASS"
        self.assertFalse(mg.adjudicate(p, [], {"sec": "claude-code/sec-pr374-1"})["ok"])
        self.assertTrue(mg.adjudicate(p, [], {"sec": "claude-code/sec-pr374-9"})["ok"])
        # Absent reviewer line is tolerated (SHOULD, not MUST).
        self.assertTrue(mg.adjudicate(PR_374, [], {"sec": "claude-code/sec-pr374-1"})["ok"])

    def test_unresolved_thread_blocks_outdated_does_not(self):
        res = mg.adjudicate(PR_374, [thread()])
        self.assertFalse(res["ok"])
        self.assertTrue(res["unresolved_threads"][0]["at_head"])
        res = mg.adjudicate(PR_374, [thread(outdated=True, oid=OLD)])
        self.assertTrue(res["ok"])
        self.assertEqual(len(res["outdated_unresolved_threads"]), 1)

    def test_review_required_blocks_empty_does_not(self):
        res = mg.adjudicate(pr(reviewDecision="REVIEW_REQUIRED"), [])
        self.assertFalse(res["ok"])
        self.assertTrue(any("REVIEW_REQUIRED" in b for b in res["blockers"]))
        self.assertTrue(mg.adjudicate(pr(reviewDecision=None), [])["ok"])

    def test_fetch_uses_recorded_shapes(self):
        calls = []

        def run(cmd):
            calls.append(cmd)
            if cmd[:3] == ["gh", "pr", "view"]:
                return json.dumps(PR_374)
            if cmd[:3] == ["gh", "api", "graphql"]:
                return json.dumps({"data": {"repository": {"pullRequest": {
                    "reviewThreads": {"nodes": THREADS_353}}}}})
            raise AssertionError(cmd)

        p, t = mg.fetch(run, 374, "NexaDuo/mARC")
        self.assertEqual(p["headRefOid"], HEAD)
        self.assertEqual(len(t), 1)
        self.assertIn("comments,headRefOid,reviewDecision,mergeStateStatus,state", calls[0])

    def test_threads_paginate(self):
        # REV-3/SEC-5 (#382): an unresolved thread on page 2 must still block.
        pages = [
            {"pageInfo": {"hasNextPage": True, "endCursor": "Y3Vyc29yOjEwMA=="},
             "nodes": [thread(resolved=True)] * 100},
            {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": [thread()]},
        ]
        calls = []

        def run(cmd):
            calls.append(cmd)
            page = pages[1] if any(a.startswith("after=") for a in cmd) else pages[0]
            return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": page}}}})

        t = mg.fetch_threads(run, "NexaDuo", "mARC", 382)
        self.assertEqual(len(t), 101)
        self.assertIn("after=Y3Vyc29yOjEwMA==", calls[1])
        self.assertFalse(mg.adjudicate(PR_374, t)["ok"])

    def test_threads_never_ending_fails_closed(self):
        def run(cmd):
            return json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {
                "pageInfo": {"hasNextPage": True, "endCursor": "x"}, "nodes": []}}}}})
        with self.assertRaises(RuntimeError):
            mg.fetch_threads(run, "o", "r", 1)

        def run2(cmd):
            return json.dumps({"data": {"repository": {"pullRequest": None}}, "errors": [{"message": "x"}]})
        with self.assertRaises(RuntimeError):
            mg.fetch_threads(run2, "o", "r", 1)

    def test_list_trusted_cli(self):
        path = os.path.join(HERE, "__nonexistent__")
        self.assertFalse(os.path.exists(path))
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(PR_374, f)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = mg.main(["--list-trusted", "--comments-file", f.name])
        os.unlink(f.name)
        self.assertEqual(rc, 0)
        self.assertEqual(len(buf.getvalue().strip().splitlines()), 4)


if __name__ == "__main__":
    unittest.main(verbosity=1)
