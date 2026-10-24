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

    def test_short_sha_prefix_ok_but_not_below_7(self):
        p = pr()
        p["comments"][3]["body"] = "## @sec review\nreviewed-sha: 2588cec\nVerdict: PASS"
        self.assertTrue(mg.adjudicate(p, [])["ok"])
        p["comments"][3]["body"] = "## @sec review\nreviewed-sha: 2588ce\nVerdict: PASS"
        self.assertFalse(mg.adjudicate(p, [])["ok"])

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
