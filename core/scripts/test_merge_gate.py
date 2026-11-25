#!/usr/bin/env python3
"""Self-test for `merge_gate.py` (origin: #378). Stdlib only; run directly:
    python3 test_merge_gate.py

Fixtures are `gh` payload SHAPES, verified on 2026-10-24:
  * `gh pr view 374 --json comments,headRefOid,reviewDecision,mergeStateStatus,state`
    (NexaDuo/mARC#374: a NONE bot comment, then @sec/@rev at an older SHA,
    then @sec PASS / @rev ADVISE at the final head). The envelope is recorded;
    the review bodies are NOT: they were rewritten to the strict grammar with
    invented reviewer ids (the real ones predate it).
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
     "body": "## @sec review\nreviewer: x/sec-1\nreviewed-sha: c10e9ea13cfb179c93da596ce0472826d7745afd\n\nScope: reshaped to the strict grammar (#378).\n\nverdict: PASS"},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T04:12:11Z", "id": "IC_kwDOTM3EE88AAAABdRgFgg",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6259475842",
     "body": "## @rev review\nreviewer: x/rev-1\nreviewed-sha: c10e9ea13cfb179c93da596ce0472826d7745afd\n\nScope: reshaped to the strict grammar (#378).\n\nverdict: PASS"},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T12:37:50Z", "id": "IC_kwDOTM3EE88AAAABdZC1mQ",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6267385241",
     "body": "## @sec review\nreviewer: x/sec-1\nreviewed-sha: 2588cec2c505b2d49162b59333741afdf3ed2634\n\nScope: reshaped to the strict grammar (#378).\n\nverdict: PASS"},
    {"author": {"login": "alexandre-machado"}, "authorAssociation": "MEMBER",
     "createdAt": "2026-10-19T12:37:50Z", "id": "IC_kwDOTM3EE88AAAABdZC1mw",
     "url": "https://github.com/NexaDuo/mARC/pull/374#issuecomment-6267385243",
     "body": "## @rev review\nreviewer: x/rev-1\nreviewed-sha: 2588cec2c505b2d49162b59333741afdf3ed2634\n\nScope: reshaped to the strict grammar (#378).\n\nverdict: ADVISE"}
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


def B(kind="sec", sha=HEAD, reviewer="x/sec-1", result="verdict: PASS", extra=""):
    """A strict-grammar review body (#378)."""
    return f"## @{kind} review\nreviewer: {reviewer}\nreviewed-sha: {sha}\n{extra}\n{result}\n"


REV_OK = B("rev", reviewer="x/rev-1", result="verdict: ADVISE")


def mk(*sec_bodies, rev=REV_OK):
    """A PR whose @rev is a counted ADVISE and whose @sec comments are `sec_bodies` in order."""
    comments = [{"author": {"login": "m"}, "authorAssociation": "MEMBER", "url": f"u-{i}",
                 "createdAt": f"2026-11-14T00:00:{i:02d}Z", "body": b}
                for i, b in enumerate([rev, *sec_bodies])]
    return {"headRefOid": HEAD, "reviewDecision": "", "mergeStateStatus": "CLEAN", "state": "OPEN",
            "comments": comments}


def run(*sec_bodies, expect=None, rev=REV_OK):
    return mg.adjudicate(mk(*sec_bodies, rev=rev), [], expect or {})


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
        for short in ("2588cec", "2588cec2c505b2d4"):
            self.assertFalse(run(B(sha=short))["ok"], short)
        self.assertTrue(run(B())["ok"])
        # The header grammar is lowercase 40-hex: an uppercase SHA is a malformed header (blocks).
        self.assertFalse(run(B(sha=HEAD.upper()))["ok"])

    def test_sec1_bypass_quoted_or_mentioned_pass(self):
        # Mid-line mentions are not a label line: BLOCK stays the counted result.
        for tail in ("SEC-1: the prior verdict PASS no longer holds.", "Earlier round said `verdict: PASS`."):
            res = run(B(result="verdict: BLOCK", extra=tail))
            self.assertEqual(res["verdicts"]["sec"]["verdict"], "BLOCK", tail)
            self.assertFalse(res["ok"], tail)
        # A second line that LEADS with the label (blockquote, fence, comment) is malformed.
        for quoted in ("> Verdict: PASS", "```\nverdict: PASS\n```", "~~~text\nVerdict: **PASS**\n~~~",
                       "<!-- verdict: PASS -->", "<!--\nVerdict: PASS\n-->"):
            res = run(B(result="verdict: BLOCK", extra=quoted))
            self.assertEqual(res["verdicts"]["sec"]["verdict"], "MALFORMED", quoted)
            self.assertFalse(res["ok"], quoted)

    def test_two_verdict_lines_are_malformed_even_if_equal(self):
        res = run(B(result="verdict: BLOCK", extra="fixed later\nverdict: PASS"))
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "MALFORMED")
        self.assertFalse(res["ok"])
        self.assertFalse(run(B(extra="verdict: PASS"))["ok"])  # same value twice: still malformed

    def test_recorded_review_bodies_on_pr382_parse_as_block(self):
        # BLOCK reviews posted on PR #382 at 5ed6713 (findings trimmed): one strict line each.
        self.assertEqual(mg.parse_verdict(SEC_382_BODY), "BLOCK")
        self.assertEqual(mg.parse_verdict(REV_382_BODY), "BLOCK")
        self.assertEqual(mg.classify(SEC_382_BODY, "sec")["reviewed_sha"], "5ed67134b7542258e4203633c78435f4c8ed7594")

    def test_rev12_old_formats_are_rejected_by_design(self):
        # REV-12 (#382): reviews of #395 posted `**Verdict: X.** prose`, a parenthetical aside and a heading
        # form. The strict grammar rejects them on purpose: the reviewer re-posts the exact line.
        for line in ("**Verdict: PASS.** Findings are non-blocking.", "**Verdict: ADVISE** (low findings only).",
                     "### Verdict: ADVISE", "**Verdict: ADVISE**", "Verdict: ADVISE", "Verdict: PASS",
                     "- verdict: PASS", "verdict: pass", "verdict:  PASS", "verdict: PASS.",
                     "verdict: PASS (note)", "verdict:PASS"):
            res = run(B(result=line))
            self.assertEqual(res["verdicts"]["sec"]["verdict"], "MALFORMED", line)
            self.assertFalse(res["ok"], line)
            self.assertTrue(any(line in b for b in res["blockers"]) or line in str(res["verdicts"]["sec"]["problem"]), line)

    def test_template_and_prose_lines_are_not_a_verdict(self):
        for line in ("verdict: PASS/ADVISE/BLOCK", "verdict: PASS|ADVISE|BLOCK", "verdict: PASS or BLOCK",
                     "Verdict: PASS would need SEC-1 fixed"):
            self.assertEqual(mg.parse_verdict(line), "MALFORMED", line)
        self.assertEqual(mg.parse_verdict("no label here"), "MALFORMED")

    def test_sec9_unbound_or_unreadable_trusted_marker_after_pass_blocks(self):
        # SEC-9 + REV-13 (#382): after a counted PASS, a later trusted marker that is not a clean
        # skip never passes silently, whatever shape its result or header has.
        cases = {
            "other reviewer bold block": B(reviewer="other/sec-9", result="**Verdict: BLOCK** - reason"),
            "other reviewer comma": B(reviewer="other/sec-9", result="**Verdict: BLOCK**, reason"),
            "unassigned block": B(reviewer="unassigned", result="verdict: BLOCK"),
            "other reviewer no result": B(reviewer="other/sec-9", result="findings only"),
            "short sha plus reason": f"## @sec review\nreviewer: x/sec-1\nreviewed-sha: {HEAD[:7]} reason\nverdict: BLOCK\n",
            "short sha": B(sha=HEAD[:7], result="verdict: BLOCK"),
            "no sha line": "## @sec review\nreviewer: x/sec-1\nverdict: BLOCK\n",
            "no result line": B(result="findings"),
            "bold sha label": f"## @sec review\nreviewer: x/sec-1\n**reviewed-sha:** {HEAD}\nverdict: BLOCK\n",
            "crlf no sha": "## @sec review\r\nreviewer: x/sec-1\r\nverdict: BLOCK\r\n",
            "same reviewer bold block": B(result="**Verdict: BLOCK** - reason"),
            "line 1 suffix": f"## @sec review (round 2)\nreviewer: x/sec-1\nreviewed-sha: {HEAD}\nverdict: BLOCK\n",
            "leading blank line": "\n" + B(result="verdict: BLOCK"),
            "marker case": B(result="verdict: BLOCK").replace("## @sec review", "## @SEC Review"),
            "other sha uppercase": B(sha=OLD.upper(), result="verdict: BLOCK"),
        }
        for label, late in cases.items():
            for expect in ({"sec": "x/sec-1"}, {}):
                res = run(B(), late, expect=expect)
                self.assertFalse(res["ok"], f"{label} {expect}")
                self.assertTrue(any("fail closed" in b or "counted result" in b for b in res["blockers"]), label)

    def test_closed_skip_list(self):
        # (a) valid header at another FULL sha: an older round, skipped even if it is a BLOCK.
        self.assertTrue(run(B(), B(sha=OLD, result="verdict: BLOCK"))["ok"])
        # (b) at HEAD, other reviewer, strict PASS/ADVISE: skipped; but a BLOCK from it is not.
        self.assertTrue(run(B(), B(reviewer="other/sec-9", result="verdict: ADVISE"), expect={"sec": "x/sec-1"})["ok"])
        self.assertFalse(run(B(), B(reviewer="other/sec-9", result="verdict: BLOCK"), expect={"sec": "x/sec-1"})["ok"])
        # Without --expect-reviewer a later HEAD marker is counted whatever the reviewer says.
        res = run(B(), B(reviewer="other/sec-9", result="verdict: BLOCK"))
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "BLOCK")

    def test_later_counted_marker_supersedes_suspects(self):
        # REV-16 (supersede half, #382): an unbound BLOCK is cleared by a later bound verdict.
        unbound = "## @sec review\nreviewer: x/sec-1\nverdict: BLOCK\n"
        self.assertFalse(run(B(), unbound)["ok"])
        self.assertTrue(run(B(), unbound, B(result="verdict: ADVISE"))["ok"])
        self.assertTrue(run(unbound, B())["ok"])  # a suspect before the counted marker is superseded too
        # A counted marker never hides a LATER suspect.
        self.assertFalse(run(unbound, B(), unbound)["ok"])

    def test_crlf_bodies_normalise(self):
        # REV-14 / SEC-18 (#382): CRLF endings (web UI) read like LF; a trailing CR cannot split fail-open.
        clean = B().replace("\n", "\r\n")
        res = run(clean)
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "PASS")
        self.assertTrue(res["ok"])
        self.assertTrue(run(B().replace("\n", "\r"))["ok"])  # lone CR too
        two = B(result="verdict: BLOCK", extra="x").replace("\n", "\r\n").rstrip("\r\n") + "\r\nverdict: PASS"
        self.assertEqual(mg.parse_verdict(two), "MALFORMED")
        self.assertFalse(run(two)["ok"])

    def test_inline_comment_opener_is_just_text(self):
        # REV-15 (#382): nothing is stripped, so a quoted opener cannot poison a clean review.
        res = run(B(extra="The parser used to choke on `<!--` in prose."))
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "PASS")
        self.assertTrue(res["ok"])

    def test_sec7_residual_hiding_shapes_are_malformed(self):
        # SEC-7 residuals (#382): degenerate comment openers, inline-code fence lookalikes and an
        # indented fence hid a displayed BLOCK from the stripper. With no stripping, every line
        # that leads with the label counts, so the second line makes the body malformed.
        for hidden in ("<!--->\nverdict: BLOCK\n-->", "<!---->\nverdict: BLOCK", "<!-- x\nverdict: BLOCK",
                       "```inline``` text\nverdict: BLOCK\n```", "    ```\nverdict: BLOCK\n    ```",
                       "```\nverdict: BLOCK\n```"):
            res = run(B(extra=hidden))
            self.assertEqual(res["verdicts"]["sec"]["verdict"], "MALFORMED", hidden)
            self.assertFalse(res["ok"], hidden)

    def test_sec16_non_ascii_lookalikes_never_pass(self):
        # SEC-16 (#382): Kelvin sign / long s / dotless i spelled values matched under Unicode case-folding.
        for value in ("BLOCK", "ADVIſE", "PİSS", "PAſſ", "ВLOCK"):
            for shape in (f"verdict: {value}", f"verdict: {value}"):
                res = run(B(result=shape))
                self.assertFalse(res["ok"], shape)
                self.assertEqual(res["verdicts"]["sec"]["verdict"], "MALFORMED", shape)
        # Fullwidth colon, no-break space and zero-width space fail closed too.
        for shape in ("verdict： PASS", "verdict:​PASS", "verdict: PASS"):
            self.assertFalse(run(B(result=shape))["ok"], shape)
        # A lookalike label line next to a strict PASS: the lookalike leads with the label -> malformed.
        self.assertFalse(run(B(extra=" verdict: BLOCK"))["ok"])

    def test_sec17_aside_pass_plus_hidden_block_is_malformed(self):
        # SEC-17 (#382): a loosely shaped PASS plus a bold BLOCK once counted as PASS.
        body = B(result="- verdict: PASS (delta only)", extra="**Verdict: BLOCK** - SEC-9 still open")
        res = run(body)
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "MALFORMED")
        self.assertFalse(res["ok"])

    def test_sec19_reviewer_line_is_required(self):
        # SEC-19 (#382): under --expect-reviewer a PASS without a (readable) reviewer line never counts,
        # and the marker must be the whole first line.
        want = {"sec": "x/sec-1"}
        for body in (f"## @sec review\nreviewed-sha: {HEAD}\nverdict: PASS\n",
                     f"## @sec review\n**reviewer:** x/sec-1\nreviewed-sha: {HEAD}\nverdict: PASS\n",
                     f"## @sec reviewer notes\nreviewer: x/sec-1\nreviewed-sha: {HEAD}\nverdict: PASS\n"):
            res = run(body, expect=want)
            self.assertFalse(res["ok"], body)
            self.assertNotIn("sec", res["verdicts"], body)
        # A missing reviewer line cannot supersede an earlier counted BLOCK either.
        self.assertFalse(run(B(result="verdict: BLOCK"), f"## @sec review\nreviewed-sha: {HEAD}\nverdict: PASS\n",
                             expect=want)["ok"])
        # Reviewer values must be equal, not prefixes.
        self.assertFalse(run(B(reviewer="x/sec-10"), expect=want)["ok"])
        self.assertTrue(run(B(), expect=want)["ok"])

    def test_untrusted_marker_never_suspect_or_counted(self):
        p = mk(B())
        p["comments"].append({"author": {"login": "z"}, "authorAssociation": "NONE", "url": "u-x",
                              "createdAt": "2026-11-14T01:00:00Z", "body": B(result="verdict: BLOCK")})
        self.assertTrue(mg.adjudicate(p, [])["ok"])

    def test_blocker_prints_offending_line(self):
        res = run(B(result="**Verdict: PASS.** fine"))
        self.assertTrue(any("**Verdict: PASS.** fine" in b and "re-posts" in b for b in res["blockers"]), res["blockers"])
        res = run(B(), "## @sec review\nreviewer: oops\nverdict: BLOCK\n")
        self.assertTrue(any("line 3 is not exactly" in b for b in res["blockers"]), res["blockers"])

    def test_block_verdict_blocks(self):
        res = run(B(), rev=B("rev", reviewer="x/rev-1", result="verdict: BLOCK"))
        self.assertFalse(res["ok"])
        self.assertTrue(any("BLOCK" in b for b in res["blockers"]))

    def test_missing_verdict_blocks(self):
        self.assertFalse(run(B(result="still reading"))["ok"])

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
        p["comments"][3]["body"] = B(reviewer="claude-code/sec-pr374-9")
        res = mg.adjudicate(p, [], {"sec": "claude-code/sec-pr374-1"})
        self.assertNotIn("sec", res["verdicts"])
        self.assertFalse(res["ok"])
        self.assertTrue(mg.adjudicate(p, [], {"sec": "claude-code/sec-pr374-9"})["ok"])

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


class Detection(unittest.TestCase):
    """SEC-20/REV-18: a trusted comment that renders like a marker heading is a
    suspect, so a later BLOCK in a near-miss shape cannot be skipped after a PASS."""

    def block_in(self, first):
        return f"{first}\nreviewer: x/sec-2\nreviewed-sha: {HEAD}\n\nverdict: BLOCK\n"

    def assert_blocks_after_pass(self, body):
        res = run(B(), body)
        self.assertFalse(res["ok"], body)
        self.assertTrue(any("cannot be counted" in b for b in res["blockers"]), res["blockers"])

    def test_near_miss_headings_are_suspects(self):
        for first in ["##  @sec review", "##\t@sec review", "## @sec\u00a0review", "\ufeff## @sec review",
                      "<!-- x -->\n## @sec review", "Posting my review.\n## @sec review",
                      "## \\@sec review", "## &#64;sec review", "## &#64;sec&nbsp;review",
                      "@sec review\n---", "<h2>@sec review</h2>", "## `@sec` review",
                      "# @sec review", "### @sec review", "## **@sec review**", "## @SEC Review",
                      "## @sec\u200b review", "## \uff20sec review"]:
            with self.subTest(first=first):
                self.assert_blocks_after_pass(self.block_in(first))

    def test_rev_kind_near_miss(self):
        res = run(B(), rev=REV_OK)
        self.assertTrue(res["ok"])
        bad = self.block_in("## `@rev` review").replace("sec-2", "rev-2")
        c = mk(B())
        c["comments"].append({"author": {"login": "m"}, "authorAssociation": "MEMBER", "url": "late",
                              "createdAt": "2026-11-14T01:00:00Z", "body": bad})
        res = mg.adjudicate(c, [], {})
        self.assertFalse(res["ok"])
        self.assertTrue(any("@rev marker cannot be counted" in b for b in res["blockers"]))

    def test_later_counted_marker_supersedes_a_near_miss(self):
        res = run(B(), self.block_in("##  @sec review"), B(reviewer="x/sec-3"))
        self.assertTrue(res["ok"], res["blockers"])

    def test_untrusted_near_miss_is_ignored(self):
        c = mk(B())
        c["comments"].append({"author": {"login": "z"}, "authorAssociation": "NONE", "url": "u",
                              "createdAt": "2026-11-14T01:00:00Z", "body": self.block_in("##  @sec review")})
        self.assertTrue(mg.adjudicate(c, [], {})["ok"])

    def test_plain_mentions_are_not_markers(self):
        for body in ["Reply to the @sec review at 5ed6713: fixed.", "thanks, see the `@rev review` above",
                     "a\nb\nc\nd\ne\n@sec review is running"]:
            with self.subTest(body=body):
                self.assertIsNone(mg.marker_kind(body))
                self.assertTrue(run(B(), body)["ok"])


class MentionFreeGrammar(unittest.TestCase):
    """#419 step 1: `## marc:<kind> review` is counted and detected like the legacy form."""

    def N(self, kind="sec", **kw):
        return B(kind, **kw).replace(f"## @{kind} review", f"## marc:{kind} review", 1)

    def test_new_form_counted(self):
        res = run(self.N())
        self.assertTrue(res["ok"], res["blockers"])
        self.assertEqual(res["verdicts"]["sec"]["verdict"], "PASS")

    def test_new_form_rev_counted_and_legacy_still_counted(self):
        res = run(B(), rev=self.N("rev", reviewer="x/rev-1", result="verdict: ADVISE"))
        self.assertTrue(res["ok"], res["blockers"])
        self.assertEqual(res["verdicts"]["rev"]["verdict"], "ADVISE")
        self.assertTrue(run(B())["ok"])

    def test_malformed_new_form_blocks_after_pass(self):
        for first in ["##  marc:sec review", "## marc:SEC review", "## Marc:sec review",
                      "## marc: sec review", "## `marc:sec` review", "Posting.\n## marc:sec review",
                      "# marc:sec review", "## marc:sec review "[:-1] + "!"]:
            body = f"{first}\nreviewer: x/sec-2\nreviewed-sha: {HEAD}\n\nverdict: BLOCK\n"
            with self.subTest(first=first):
                res = run(B(), body)
                self.assertFalse(res["ok"], first)
                self.assertTrue(any("cannot be counted" in b for b in res["blockers"]))

    def test_malformed_body_in_new_form_blocks(self):
        bad = self.N(result="verdict: pass")
        self.assertFalse(run(bad)["ok"])

    def test_new_form_from_non_collaborator_ignored(self):
        forged = self.N(reviewer="x/sec-9", result="verdict: BLOCK")
        c = mk(B())
        c["comments"].append({"author": {"login": "z"}, "authorAssociation": "NONE", "url": "u",
                              "createdAt": "2026-11-14T01:00:00Z", "body": forged})
        res = mg.adjudicate(c, [], {})
        self.assertTrue(res["ok"], res["blockers"])
        self.assertTrue(any(i["reason"] == "untrusted author association" for i in res["ignored_markers"]))
        c["comments"][-1].pop("authorAssociation")  # missing association fails closed
        self.assertTrue(mg.adjudicate(c, [], {})["ok"])

    def test_mixed_supersession_latest_wins_either_order(self):
        blk_new = self.N(reviewer="x/sec-2", result="verdict: BLOCK")
        blk_old = B(reviewer="x/sec-2", result="verdict: BLOCK")
        self.assertFalse(run(B(), blk_new)["ok"])
        self.assertTrue(run(blk_new, B(reviewer="x/sec-3"))["ok"])
        self.assertFalse(run(self.N(), blk_old)["ok"])
        self.assertTrue(run(blk_old, self.N(reviewer="x/sec-3"))["ok"])

    def test_malformed_new_form_superseded_by_legacy_counted(self):
        bad = f"##  marc:sec review\nreviewer: x/sec-2\nreviewed-sha: {HEAD}\n\nverdict: BLOCK\n"
        self.assertTrue(run(bad, B(reviewer="x/sec-3"))["ok"])

    def test_expect_reviewer_applies_to_new_form(self):
        res = run(self.N(reviewer="x/sec-1"), expect={"sec": "x/sec-other"})
        self.assertFalse(res["ok"])

    def test_list_trusted_sees_both_forms(self):
        cs = [{"body": self.N(), "authorAssociation": "MEMBER"}, {"body": B(), "authorAssociation": "MEMBER"},
              {"body": self.N(), "authorAssociation": "NONE"}]
        self.assertEqual(len(mg.trusted_markers(cs)), 2)

    NEAR_MISS = ["## marc sec review", "## marc-sec review", "## marc/sec review", "## marc_sec review",
                 "## marcsec review", "## marc::sec review", "## marc:@sec review",
                 "## marc: sec-review", "## Marc Sec Review"]

    def test_loose_near_misses_fail_closed_after_pass(self):
        for first in self.NEAR_MISS:
            body = f"{first}\nreviewer: x/sec-2\nreviewed-sha: {HEAD}\n\nverdict: BLOCK\n"
            with self.subTest(first=first):
                res = run(B(), body)
                self.assertFalse(res["ok"], first)
                self.assertTrue(any("cannot be counted" in b for b in res["blockers"]))

    def test_loose_near_miss_rev_kind(self):
        c = mk(B())
        c["comments"].append({"author": {"login": "m"}, "authorAssociation": "MEMBER", "url": "late",
                              "createdAt": "2026-11-14T01:00:00Z",
                              "body": f"## marc-rev review\nreviewer: x/rev-2\nreviewed-sha: {HEAD}\n\nverdict: BLOCK\n"})
        self.assertFalse(mg.adjudicate(c, [], {})["ok"])

    def test_prose_is_not_a_marker(self):
        for body in ["marc security review of the change", "## marc secure review", "marc second review",
                     "## Review of marc sections"]:
            with self.subTest(body=body):
                self.assertIsNone(mg.marker_kind(body))
                self.assertTrue(run(B(), body + "\n\nverdict: BLOCK\n")["ok"])

    def test_missing_marker_text_names_mention_free_first(self):
        res = mg.adjudicate(mk(), [], {})
        self.assertTrue(any("`## marc:sec review` (or `## @sec review`)" in b for b in res["blockers"]), res["blockers"])

    def test_header_error_names_mention_free_first(self):
        for kind in mg.KINDS:
            error = mg.classify("## bad header", kind)["header_error"]
            self.assertTrue(error.startswith(
                f"line 1 is not exactly `## marc:{kind} review` (or `## @{kind} review`)"), error)


if __name__ == "__main__":
    unittest.main(verbosity=1)
