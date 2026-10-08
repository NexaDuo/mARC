#!/usr/bin/env python3
"""Self-test for scripts/codex_rollout_tokens.py (issue #346).

Every fixture here is synthetic and built in-process: no real rollout,
transcript, prompt, or user content is read or committed. Covers the cases
the issue calls out: cumulative vs per-request counters, duplicate records
and re-emitted token_count events, `compacted` (including a counter reset),
poll-turn counting (real function_call shapes, compound
commands), records without response_id or compaction id, and the per-role/per-round specialist summary.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codex_rollout_tokens as crt  # noqa: E402


def u(inp, cached, out, reasoning=0):
    return {"input_tokens": inp, "cached_input_tokens": cached, "output_tokens": out,
            "reasoning_output_tokens": reasoning, "total_tokens": inp + out}


def add(a, b):
    return {k: a.get(k, 0) + b.get(k, 0) for k in set(a) | set(b)}


class Rollout:
    """Builds a synthetic rollout. Each request emits a token_usage_record
    (optional) and a token_count event, the way codex-cli 0.160 does."""

    def __init__(self, thread_id="thread-op", records=True, first_user="Fix the bug."):
        self.rows = [{"type": "session_meta", "timestamp": "2026-01-01T00:00:00Z",
                      "payload": {"id": thread_id, "timestamp": "2026-01-01T00:00:00Z"}}]
        self.records = records
        self.total = u(0, 0, 0)
        self.n = 0
        self.turn("turn-1")
        self.user(first_user)

    def turn(self, tid):
        self.rows.append({"type": "turn_context", "payload": {"turn_id": tid}})
        self.tid = tid

    def user(self, text):
        self.rows.append({"type": "response_item", "payload": {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": text}]}})

    def call(self, cid, typ="custom_tool_call", name="exec", inp=""):
        self.rows.append({"type": "response_item", "payload": {
            "type": typ, "call_id": cid, "name": name, "input": inp, "arguments": inp}})
        self.rows.append({"type": "response_item", "payload": {
            "type": typ + "_output", "call_id": cid, "output": "x"}})

    def request(self, usage, rid=None, dup_record=False, dup_count=False, reset=False):
        self.n += 1
        rid = rid or f"resp-{self.n}"
        self.total = usage if reset else add(self.total, usage)
        rec = {"type": "token_usage_record", "payload": {
            "response_id": rid, "turn_id": self.tid, "usage": usage, "thread_token_usage": dict(self.total)}}
        if self.records:
            self.rows.append(rec)
            if dup_record:
                self.rows.append(json.loads(json.dumps(rec)))
        tc = {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": dict(self.total), "last_token_usage": usage}}}
        self.rows.append(tc)
        if dup_count:
            self.rows.append(json.loads(json.dumps(tc)))
        return rid

    def compacted(self, rid):
        self.rows.append({"type": "compacted", "payload": {"compaction_response_id": rid}})

    def write(self, path):
        with open(path, "w", encoding="utf-8") as fh:
            for r in self.rows:
                fh.write(json.dumps(r) + "\n")
            fh.write("not json\n")  # tolerated
        return path


def scenario(records=True, reset=False):
    r = Rollout(records=records)
    r.request(u(1000, 0, 50), dup_record=True, dup_count=True)          # user turn start
    r.call("c1", inp="python3 plugins/x/scripts/dispatch_agent.py --role dev --prompt p")
    r.request(u(1200, 1000, 40))                                         # dispatch
    r.call("c2", typ="function_call", name="sleep", inp='{"seconds": 30}')
    r.request(u(1250, 1200, 10))                                         # poll
    r.call("c3", inp="sleep 60")
    r.request(u(1300, 1250, 10), dup_count=True)                         # poll (shell)
    r.call("c4", name="write_stdin", inp='{"session_id": 3, "chars": ""}')
    r.request(u(1310, 1300, 5))                                          # poll (empty stdin)
    r.compacted("resp-6")
    r.request(u(400, 0, 100), reset=reset)                               # compaction request
    r.turn("turn-2")
    r.user("Continue.")
    r.request(u(600, 400, 30))
    return r


EXPECT = {"input_tokens": 1000 + 1200 + 1250 + 1300 + 1310 + 400 + 600,
          "cached_input_tokens": 0 + 1000 + 1200 + 1250 + 1300 + 0 + 400,
          "output_tokens": 50 + 40 + 10 + 10 + 5 + 100 + 30}


class ParserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def path(self, name):
        return os.path.join(self.tmp.name, name)

    def check_totals(self, res):
        for k, v in EXPECT.items():
            self.assertEqual(res[k], v, k)
        self.assertEqual(res["uncached_input_tokens"], EXPECT["input_tokens"] - EXPECT["cached_input_tokens"])
        self.assertEqual(res["requests"], 7)
        self.assertEqual(res["turns"], 2)
        self.assertEqual(res["poll_turns"], 3)
        self.assertEqual(res["compactions"], 1)

    def test_records_dedup_by_response_id(self):
        res = crt.parse_rollout(scenario().write(self.path("r.jsonl")))
        self.check_totals(res)
        self.assertEqual(res["checks"]["source"], "token_usage_record")
        self.assertEqual(res["checks"]["duplicates_skipped"], 1)
        self.assertEqual(res["checks"]["cumulative_mismatch"], 0)
        self.assertIn("compaction", res["by_bucket"])
        self.assertIn("dispatch", res["by_bucket"])
        self.assertAlmostEqual(res["by_bucket"]["poll"]["requests"], 3)

    def test_token_count_fallback_dedups_cumulative(self):
        res = crt.parse_rollout(scenario(records=False).write(self.path("tc.jsonl")))
        self.assertEqual(res["checks"]["source"], "token_count")
        self.assertEqual(res["checks"]["duplicates_skipped"], 2)  # two re-emitted events
        self.assertEqual(res["checks"]["cumulative_mismatch"], 0)
        self.assertIn("compaction", res["by_bucket"])
        self.check_totals(res)

    def test_fallback_matches_records(self):
        a = crt.parse_rollout(scenario().write(self.path("a.jsonl")))
        b = crt.parse_rollout(scenario(records=False).write(self.path("b.jsonl")))
        for k in ("input_tokens", "cached_input_tokens", "output_tokens", "requests", "poll_turns", "weighted"):
            self.assertEqual(a[k], b[k], k)

    def test_cumulative_reset_after_compaction(self):
        for records in (True, False):
            res = crt.parse_rollout(scenario(records=records, reset=True).write(self.path(f"z{records}.jsonl")))
            self.check_totals(res)  # per-request values trusted, no negative delta
            self.assertEqual(res["checks"]["cumulative_resets"], 1)

    def test_cumulative_mismatch_detected(self):
        r = scenario()
        r.rows[-2]["payload"]["thread_token_usage"]["output_tokens"] += 7  # corrupt one cumulative counter
        res = crt.parse_rollout(r.write(self.path("m.jsonl")))
        self.assertGreaterEqual(res["checks"]["cumulative_mismatch"], 1)
        self.assertEqual(res["output_tokens"], EXPECT["output_tokens"])  # per-request is authoritative

    def test_null_info_token_count_ignored(self):
        r = Rollout(records=False)
        r.rows.append({"type": "event_msg", "payload": {"type": "token_count", "info": None}})
        r.request(u(10, 0, 1))
        res = crt.parse_rollout(r.write(self.path("n.jsonl")))
        self.assertEqual(res["input_tokens"], 10)
        self.assertEqual(res["requests"], 1)

    def test_weighted_matches_claude_arm_formula(self):
        t = {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 100}
        self.assertEqual(crt.codex_weighted(t), crt.weighted_usage_tokens(300, 800))

    def test_summarize_roles_and_rounds(self):
        home = self.path("home")
        day = os.path.join(home, "sessions", "2026", "01", "01")
        os.makedirs(day)
        scenario().write(os.path.join(day, "rollout-2026-01-01T00-00-00-thread-op.jsonl"))
        for i, ts in enumerate(("2026-01-01T00:01:00Z", "2026-01-01T00:05:00Z")):
            dev = Rollout(thread_id=f"thread-dev{i}", first_user="Act as the mARC engineer specialist. Fix it.")
            dev.rows[0]["payload"]["timestamp"] = ts
            dev.request(u(100 * (i + 1), 0, 10))
            dev.write(os.path.join(day, f"rollout-x-thread-dev{i}.jsonl"))
        events = self.path("events.jsonl")
        with open(events, "w") as fh:
            fh.write(json.dumps({"type": "thread.started", "thread_id": "thread-op"}) + "\n")
        projects = self.path("projects")
        os.makedirs(os.path.join(projects, "slug"))
        ledger = self.path("ledger.jsonl")
        with open(ledger, "w") as fh:
            for sid, role, ts in (("s1", "security", "2026-01-01T00:02:00Z"), ("s2", "review", "2026-01-01T00:02:01Z"),
                                  ("s3", "security", "2026-01-01T00:06:00Z"), ("gone", "review", "2026-01-01T00:06:01Z")):
                fh.write(json.dumps({"harness": "claude", "role": role, "session_id": sid, "ts": ts}) + "\n")
        for sid in ("s1", "s2", "s3"):
            with open(os.path.join(projects, "slug", f"{sid}.jsonl"), "w") as fh:
                msg = {"id": "m1", "usage": {"input_tokens": 5, "cache_creation_input_tokens": 20,
                                             "cache_read_input_tokens": 100, "output_tokens": 7}}
                for _ in range(3):  # one line per content block: same usage repeated
                    fh.write(json.dumps({"type": "assistant", "timestamp": "t", "message": msg}) + "\n")
        res = crt.summarize(events, home, ledger, projects, "A")
        self.assertEqual(res["operator"]["input_tokens"], EXPECT["input_tokens"])
        roles = res["specialists_by_role_round"]
        self.assertEqual(sorted(roles), ["engineer", "review", "security"])
        self.assertEqual(sorted(roles["engineer"]), ["round1", "round2"])
        self.assertEqual(sorted(roles["security"]), ["round1", "round2"])
        claude_w = crt.weighted_usage_tokens(32, 100)
        self.assertEqual(roles["security"]["round1"], claude_w)  # deduped by message.id, not x3
        self.assertEqual(res["specialist_transcripts_missing"], 1)
        self.assertEqual(res["specialist_invocations"], 5)
        self.assertEqual(res["weighted"], res["operator"]["weighted"] + res["specialist_weighted"])

    def test_poll_classifier_real_function_call_shapes(self):
        # REV-3/REV-5 (PR #347): the real exec_command / shell argument shapes,
        # and a compound command that did real work before sleeping.
        def fc(name, args):
            return crt.classify_call({"type": "function_call", "name": name, "arguments": json.dumps(args)})
        self.assertEqual(fc("exec_command", {"cmd": "sleep 30"}), "poll")
        self.assertEqual(fc("exec_command", {"cmd": "sleep 5 && sleep 5"}), "poll")
        self.assertEqual(fc("shell", {"command": ["bash", "-lc", "sleep 20"]}), "poll")
        self.assertEqual(fc("exec_command", {"cmd": "bash -lc 'sleep 10'"}), "poll")
        self.assertNotEqual(fc("exec_command", {"cmd": "python3 -m unittest; sleep 1"}), "poll")
        self.assertNotEqual(fc("shell", {"command": ["bash", "-lc", "make test && sleep 2"]}), "poll")
        self.assertNotEqual(crt.classify_call({"type": "custom_tool_call", "name": "exec",
                                               "input": "python3 -m unittest; sleep 1"}), "poll")
        self.assertEqual(fc("write_stdin", {"session_id": 3, "chars": ""}), "poll")
        self.assertNotEqual(fc("write_stdin", {"session_id": 3, "chars": "\u0003"}), "poll")
        self.assertEqual(fc("exec_command", {"cmd": "gh run watch 123"}), "poll")

    def test_compound_sleep_not_counted_as_poll_turn(self):
        r = Rollout()
        r.request(u(100, 0, 5))
        r.call("c1", typ="function_call", name="exec_command", inp=json.dumps({"cmd": "python3 -m unittest; sleep 1"}))
        r.request(u(150, 100, 5))
        r.call("c2", typ="function_call", name="exec_command", inp=json.dumps({"cmd": "sleep 30"}))
        r.request(u(160, 150, 5))
        res = crt.parse_rollout(r.write(self.path("p.jsonl")))
        self.assertEqual(res["poll_turns"], 1)

    def test_record_without_response_id_deduped(self):
        # REV-4: an id-less record re-emitted verbatim counts once; two
        # distinct id-less requests both count.
        r = Rollout()
        r.request(u(100, 0, 5), dup_record=True)
        r.request(u(150, 100, 5))
        for row in r.rows:
            if row["type"] == "token_usage_record":
                row["payload"].pop("response_id")
        res = crt.parse_rollout(r.write(self.path("noid.jsonl")))
        self.assertEqual(res["checks"]["source"], "token_usage_record")
        self.assertEqual(res["checks"]["duplicates_skipped"], 1)
        self.assertEqual(res["requests"], 2)
        self.assertEqual(res["input_tokens"], 250)

    def test_compacted_without_id_in_records_mode(self):
        # REV-4: a compacted record with no compaction_response_id still
        # charges the next request to the compaction bucket in records mode.
        r = Rollout()
        r.request(u(1000, 0, 50))
        r.rows.append({"type": "compacted", "payload": {}})
        r.request(u(400, 0, 100))
        r.request(u(450, 400, 10))
        res = crt.parse_rollout(r.write(self.path("cnoid.jsonl")))
        self.assertEqual(res["checks"]["source"], "token_usage_record")
        self.assertEqual(res["compactions"], 1)
        self.assertEqual(res["by_bucket"]["compaction"]["requests"], 1)
        self.assertEqual(res["by_bucket"]["compaction"]["input_tokens"], 400)

    def test_cli_summarize_emits_one_json_line(self):
        home = self.path("h2")
        os.makedirs(os.path.join(home, "sessions"))
        scenario().write(os.path.join(home, "sessions", "rollout-a-thread-op.jsonl"))
        import contextlib
        import io
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            crt.main(["summarize", "--codex-home", home])
        lines = buf.getvalue().strip().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("weighted", json.loads(lines[0]))


if __name__ == "__main__":
    unittest.main()
