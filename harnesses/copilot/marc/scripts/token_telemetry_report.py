#!/usr/bin/env python3
"""Zero-dependency summary report over `token-telemetry.jsonl` (origin: #149).

`rtk gain`-style: a plain stdlib table, no dashboard, no external deps. Reads
the append-only JSONL the Stop-hook recorder (and the backfill script) write,
groups by `session_id`, and prints a per-session roll-up plus a per-turn
table for the most recent session, so an operator can see token-efficiency
trends over time without standing up any visualization tooling. Rich
charts/SVGs are explicitly out of scope for v1 (#149 AC#6).

Usage:
    python3 token_telemetry_report.py [--sessions N] [--path FILE]
        --sessions N   how many most-recent sessions to summarize (default 10)
        --path FILE    override the telemetry file (default: $MARC_STATE_DIR/
                       token-telemetry.jsonl, or ~/.claude/marc-state/... )

Exits 0 always (a report tool, not a gate) — exits 1 only if the file cannot
be read at all (distinct from "empty", which is a normal opt-in-not-yet-used
state and prints a friendly message instead).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_telemetry import telemetry_path  # noqa: E402


def load_records(path: str) -> list[dict]:
    records: list[dict] = []
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(rec, dict):
                records.append(rec)
    return records


def group_by_session(records: list[dict]) -> dict[str, list[dict]]:
    sessions: dict[str, list[dict]] = {}
    for rec in records:
        sid = str(rec.get("session_id", "-"))
        sessions.setdefault(sid, []).append(rec)
    for turns in sessions.values():
        turns.sort(key=lambda r: r.get("turn_index", 0))
    return sessions


def session_totals(turns: list[dict]) -> dict:
    total = {"turns": len(turns), "input": 0, "output": 0, "cache_read": 0,
             "cache_write": 0, "weighted": 0, "first_ts": None, "last_ts": None,
             "model": "-", "repo": "-"}
    for t in turns:
        total["input"] += int(t.get("input", 0) or 0)
        total["output"] += int(t.get("output", 0) or 0)
        total["cache_read"] += int(t.get("cache_read", 0) or 0)
        total["cache_write"] += int(t.get("cache_write", 0) or 0)
        total["weighted"] += int(t.get("weighted", 0) or 0)
        ts = t.get("ts")
        if isinstance(ts, (int, float)):
            if total["first_ts"] is None or ts < total["first_ts"]:
                total["first_ts"] = ts
            if total["last_ts"] is None or ts > total["last_ts"]:
                total["last_ts"] = ts
        if t.get("model") and t["model"] != "-":
            total["model"] = t["model"]
        if t.get("repo") and t["repo"] != "-":
            total["repo"] = t["repo"]
    return total


def fmt_ts(ts) -> str:
    if not isinstance(ts, (int, float)):
        return "-"
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except (OverflowError, OSError, ValueError):
        return "-"


def print_report(sessions: dict[str, list[dict]], *, limit: int) -> None:
    ordered = sorted(
        sessions.items(),
        key=lambda kv: max((t.get("ts", 0) or 0) for t in kv[1]),
        reverse=True,
    )[:limit]

    print(f"{'session':<14} {'repo':<24} {'model':<26} {'turns':>6} "
          f"{'weighted':>12} {'input':>10} {'output':>10} {'cache_read':>12} "
          f"{'cache_write':>12}  last")
    grand_weighted = 0
    grand_turns = 0
    for sid, turns in ordered:
        totals = session_totals(turns)
        grand_weighted += totals["weighted"]
        grand_turns += totals["turns"]
        print(f"{sid[:12]:<14} {totals['repo'][:24]:<24} {totals['model'][:26]:<26} "
              f"{totals['turns']:>6} {totals['weighted']:>12} {totals['input']:>10} "
              f"{totals['output']:>10} {totals['cache_read']:>12} "
              f"{totals['cache_write']:>12}  {fmt_ts(totals['last_ts'])}")

    print(f"\n{len(ordered)} session(s) shown, {len(sessions)} total in file. "
          f"weighted tokens (shown sessions): {grand_weighted}   turns: {grand_turns}")

    if len(ordered) >= 2:
        newest = session_totals(ordered[0][1])
        oldest = session_totals(ordered[-1][1])
        if oldest["turns"] and newest["turns"]:
            newest_avg = newest["weighted"] / newest["turns"]
            oldest_avg = oldest["weighted"] / oldest["turns"]
            if oldest_avg > 0:
                delta_pct = (newest_avg - oldest_avg) / oldest_avg * 100
                direction = "up" if delta_pct > 0 else "down"
                print(f"\nsimple trend: newest shown session averages "
                      f"{newest_avg:.0f} weighted tokens/turn vs oldest shown "
                      f"session's {oldest_avg:.0f} ({direction} {abs(delta_pct):.0f}%).")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sessions", type=int, default=10,
                     help="how many most-recent sessions to summarize (default 10)")
    ap.add_argument("--path", default=None, help="override the telemetry JSONL path")
    args = ap.parse_args(argv)

    path = args.path or telemetry_path()
    if not os.path.isfile(path):
        print(f"no telemetry file at {path} — telemetry is opt-in and OFF by "
              f"default; enable it with `[telemetry] enabled = true` in "
              f".claude/team.toml to start recording, or run "
              f"token_telemetry_backfill.py to mine existing session history.")
        return 0

    try:
        records = load_records(path)
    except OSError as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return 1

    if not records:
        print(f"{path} exists but has no records yet.")
        return 0

    sessions = group_by_session(records)
    print_report(sessions, limit=max(1, args.sessions))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
