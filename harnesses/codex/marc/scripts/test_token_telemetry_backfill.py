#!/usr/bin/env python3
"""Self-test for `token_telemetry_backfill.py` (origin: #149).

Deterministic, offline, zero token cost. Synthesizes a fake
`~/.claude/projects/<encoded>/<session>.jsonl` tree (no real paths/names) and
asserts the backfill: (a) writes one record per turn per session on a first
run, (b) is IDEMPOTENT — a second run against the SAME transcripts appends
NOTHING new, (c) still picks up genuinely NEW turns/sessions appended after
the first run, and (d) `--dry-run` writes nothing at all.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import token_telemetry_backfill as backfill  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {msg}")
    if not cond:
        _failures.append(msg)


def write_transcript(path: str, turns: list[dict]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for i, t in enumerate(turns):
            fh.write(json.dumps({"type": "user", "message": {"content": f"turn {i}"}}) + "\n")
            fh.write(json.dumps({
                "type": "assistant",
                "timestamp": "2026-07-21T00:00:00.000Z",
                "message": {
                    "model": t["model"],
                    "content": [],
                    "usage": {
                        "input_tokens": t.get("input", 0),
                        "output_tokens": t.get("output", 0),
                        "cache_read_input_tokens": t.get("cache_read", 0),
                        "cache_creation_input_tokens": t.get("cache_write", 0),
                    },
                },
            }) + "\n")


def read_lines(path: str) -> list[dict]:
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as fh:
        return [json.loads(ln) for ln in fh.read().splitlines() if ln.strip()]


def main() -> int:
    with tempfile.TemporaryDirectory() as base:
        projects_root = os.path.join(base, "projects")
        session_a = os.path.join(projects_root, "-encoded-project-", "session-aaa.jsonl")
        session_b = os.path.join(projects_root, "-encoded-project-", "session-bbb.jsonl")
        write_transcript(session_a, [
            {"model": "claude-opus-4-8-20260101", "input": 100, "output": 20,
             "cache_read": 0, "cache_write": 0},
            {"model": "claude-opus-4-8-20260101", "input": 50, "output": 10,
             "cache_read": 10, "cache_write": 0},
        ])
        write_transcript(session_b, [
            {"model": "claude-sonnet-4-5", "input": 5, "output": 5,
             "cache_read": 0, "cache_write": 0},
        ])

        target = os.path.join(base, "state", "token-telemetry.jsonl")

        # (d) --dry-run writes nothing.
        written_dry, sessions_dry = backfill.backfill(projects_root, target, dry_run=True)
        check(written_dry == 3, f"dry-run reports 3 would-be records, got {written_dry}")
        check(sessions_dry == 2, f"dry-run scans 2 sessions, got {sessions_dry}")
        check(not os.path.exists(target), "dry-run writes NOTHING to disk")

        # (a) first real run writes one record per turn (2 + 1 = 3).
        written1, sessions1 = backfill.backfill(projects_root, target, dry_run=False)
        check(written1 == 3, f"first run writes 3 records (2 turns + 1 turn), got {written1}")
        check(sessions1 == 2, f"first run scans 2 sessions, got {sessions1}")
        lines1 = read_lines(target)
        check(len(lines1) == 3, f"target file has 3 lines after first run, got {len(lines1)}")

        # (b) second run against the SAME transcripts appends nothing new.
        written2, sessions2 = backfill.backfill(projects_root, target, dry_run=False)
        check(written2 == 0, f"second run (idempotent) writes 0 NEW records, got {written2}")
        lines2 = read_lines(target)
        check(len(lines2) == 3, f"target file STILL has 3 lines after a re-run, got {len(lines2)}")
        check(lines1 == lines2, "re-run does not alter existing lines (byte-for-byte stable)")

        # (c) a genuinely NEW turn appended to an existing session IS picked up.
        write_transcript(session_b, [
            {"model": "claude-sonnet-4-5", "input": 5, "output": 5,
             "cache_read": 0, "cache_write": 0},
            {"model": "claude-sonnet-4-5", "input": 7, "output": 3,
             "cache_read": 0, "cache_write": 0},
        ])
        written3, _ = backfill.backfill(projects_root, target, dry_run=False)
        check(written3 == 1, f"a newly appended turn is picked up exactly once, got {written3}")
        lines3 = read_lines(target)
        check(len(lines3) == 4, f"target file grows by exactly 1 line, got {len(lines3)}")

        # Records never carry a 'prompt'/message-body field (privacy boundary).
        check(all("prompt" not in ln and "message" not in ln for ln in lines3),
              "backfilled records never carry message-body content")

        # No projects directory at all -> silent, harmless (missing history).
        empty_root = os.path.join(base, "no-such-projects-dir")
        written4, sessions4 = backfill.backfill(empty_root, os.path.join(base, "unused.jsonl"))
        check(written4 == 0 and sessions4 == 0, "missing projects root -> 0 records, 0 sessions, no crash")

        # #402: subagent files in the real layout are folded in, once; a corrupt
        # sibling transcript is skipped with a stderr warning, not fatal.
        with tempfile.TemporaryDirectory() as b2:
            root2 = os.path.join(b2, "projects")
            sess = os.path.join(root2, "-enc-", "sess-sub.jsonl")
            write_transcript(sess, [{"model": "m", "input": 1, "output": 1,
                                     "cache_read": 0, "cache_write": 0}])
            subdir = os.path.join(root2, "-enc-", "sess-sub", "subagents")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "agent-x.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"usage": {
                    "cache_creation_input_tokens": 100,
                    "cache_creation": {"ephemeral_5m_input_tokens": 100}}}}) + "\n")
            t2 = os.path.join(b2, "t.jsonl")
            backfill.backfill(root2, t2)
            backfill.backfill(root2, t2)
            recs = read_lines(t2)
            check(len(recs) == 1 and recs[0]["sc_cache_write"] == 100
                  and recs[0]["sc_cache_write_weighted"] == 125
                  and recs[0]["schema_version"] == 2,
                  "backfill folds subagents/ files once and stays idempotent")

        # #402: late subagent usage -> the turn is re-emitted ONCE; re-run is a no-op.
        with tempfile.TemporaryDirectory() as b3:
            root3 = os.path.join(b3, "projects")
            sess = os.path.join(root3, "-enc-", "sess-late.jsonl")
            write_transcript(sess, [{"model": "m", "input": 1, "output": 1,
                                     "cache_read": 0, "cache_write": 0}])
            t3 = os.path.join(b3, "t.jsonl")
            backfill.backfill(root3, t3)
            subdir = os.path.join(root3, "-enc-", "sess-late", "subagents")
            os.makedirs(subdir)
            with open(os.path.join(subdir, "agent-x.jsonl"), "w", encoding="utf-8") as fh:
                fh.write(json.dumps({"type": "assistant", "message": {"usage": {
                    "cache_creation_input_tokens": 100}}}) + "\n")
            w1, _ = backfill.backfill(root3, t3)
            w2, _ = backfill.backfill(root3, t3)
            recs = read_lines(t3)
            check(w1 == 1 and w2 == 0 and len(recs) == 2
                  and recs[-1]["sc_cache_write"] == 100,
                  "backfill re-emits a changed turn once and then is a no-op")

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll token-telemetry-backfill self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
