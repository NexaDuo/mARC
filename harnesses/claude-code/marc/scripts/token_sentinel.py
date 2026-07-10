#!/usr/bin/env python3
"""Token-throughput sentinel for Claude Code session logs.

Offline operator self-check: no network, zero token/dollar cost. Reads a Claude
Code session `.jsonl` and reports, per user turn, the model used, the number of
assistant tool calls, and the tokens processed (input + cache_read + cache_write),
flagging turns that look runaway so the operator can tighten model tiering and
dispatch bounds.

Usage:
    python3 token_sentinel.py [SESSION.jsonl]
      SESSION.jsonl   explicit path to a session log; if omitted, the newest
                      `.jsonl` for the current working directory's project under
                      ~/.claude/projects/<encoded>/ is used (falling back to the
                      newest log across all projects).

    Thresholds (override with flags):
      --calls N       flag a turn with more than N assistant tool calls  (default 25)
      --tokens N      flag a turn processing more than N tokens           (default 300000)

The per-turn token metric sums, over every assistant message in the turn,
`usage.input_tokens + usage.cache_read_input_tokens +
usage.cache_creation_input_tokens`. A "turn" is the span of assistant activity
following one real user message (tool-result feedback messages are not new turns).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys


def encoded_project_dir(cwd: str) -> str:
    """Mirror Claude Code's project-dir encoding: non-alphanumerics -> '-'."""
    return re.sub(r"[^A-Za-z0-9]", "-", os.path.abspath(cwd))


def newest_session_log(cwd: str) -> str | None:
    home = os.path.expanduser("~")
    projects_root = os.path.join(home, ".claude", "projects")
    candidates: list[str] = []
    encoded = os.path.join(projects_root, encoded_project_dir(cwd))
    if os.path.isdir(encoded):
        candidates = glob.glob(os.path.join(encoded, "*.jsonl"))
    if not candidates:
        # Fall back to the newest log across every project.
        candidates = glob.glob(os.path.join(projects_root, "*", "*.jsonl"))
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def is_real_user_turn(rec: dict) -> bool:
    """A real user turn is a user message whose content is not tool-result feedback."""
    if rec.get("type") != "user":
        return False
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, list):
        # If every block is a tool_result, this is loop feedback, not a new turn.
        types = {b.get("type") for b in content if isinstance(b, dict)}
        if types and types <= {"tool_result"}:
            return False
    return True


def usage_tokens(usage: dict) -> int:
    return (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )


def count_tool_calls(content) -> int:
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if isinstance(b, dict) and b.get("type") == "tool_use")


def user_text(rec: dict) -> str:
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        return " ".join(p for p in parts if p)
    return ""


def analyze(path: str):
    turns: list[dict] = []
    current: dict | None = None
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if is_real_user_turn(rec):
                current = {
                    "prompt": (user_text(rec)[:60] or "(non-text prompt)"),
                    "model": "-",
                    "calls": 0,
                    "tokens": 0,
                }
                turns.append(current)
                continue
            if rec.get("type") == "assistant":
                if current is None:
                    current = {"prompt": "(pre-first-user)", "model": "-", "calls": 0, "tokens": 0}
                    turns.append(current)
                msg = rec.get("message") or {}
                model = msg.get("model")
                if model:
                    current["model"] = model
                current["calls"] += count_tool_calls(msg.get("content"))
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    current["tokens"] += usage_tokens(usage)
    return turns


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-turn token/tool-call sentinel for Claude Code logs.")
    ap.add_argument("session", nargs="?", help="path to a session .jsonl (default: newest for this project)")
    ap.add_argument("--calls", type=int, default=25, help="flag turns above this tool-call count")
    ap.add_argument("--tokens", type=int, default=300_000, help="flag turns above this token count")
    args = ap.parse_args(argv)

    path = args.session or newest_session_log(os.getcwd())
    if not path:
        print("no session .jsonl found (pass an explicit path)", file=sys.stderr)
        return 2
    if not os.path.isfile(path):
        print(f"not a file: {path}", file=sys.stderr)
        return 2

    turns = analyze(path)
    print(f"session: {path}")
    print(f"turns:   {len(turns)}   thresholds: >{args.calls} calls, >{args.tokens} tokens\n")
    print(f"{'#':>3}  {'model':<28} {'calls':>6} {'tokens':>12}  flag  prompt")
    flagged = 0
    total_tokens = 0
    for i, t in enumerate(turns, 1):
        total_tokens += t["tokens"]
        runaway = t["calls"] > args.calls or t["tokens"] > args.tokens
        flag = "RUNAWAY" if runaway else ""
        if runaway:
            flagged += 1
        print(f"{i:>3}  {t['model']:<28} {t['calls']:>6} {t['tokens']:>12}  {flag:<7} {t['prompt']}")
    print(f"\ntotal tokens processed: {total_tokens}   flagged turns: {flagged}")
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
