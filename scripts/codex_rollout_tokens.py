#!/usr/bin/env python3
"""Token accounting for one Codex benchmark run (issue #346).

Stdlib only. Used by the Codex arm of scripts/run_token_benchmark.sh to turn
one operator thread (a Codex rollout) plus the specialists it dispatched into
ONE summary record, appended as a JSON line to the arm's result file. The
report (scripts/benchmark_report.py) then takes the median of N such records.

Rollout semantics (checked against a codex-cli 0.160 rollout's record shapes;
only key names were inspected, never content):

  * `token_usage_record.usage` is ONE model request, keyed by `response_id`.
    The same record can be written more than once; count each response_id
    once.
  * `token_usage_record.thread_token_usage` is cumulative over the thread.
  * `event_msg/token_count.info.last_token_usage` repeats the latest request's
    usage and `info.total_token_usage` repeats the cumulative counter. A
    token_count event is re-emitted without a new request (cumulative total
    unchanged), so these are deduplicated on the cumulative counter. They are
    the fallback source when a rollout has no token_usage_record at all.
  * `input_tokens` INCLUDES `cached_input_tokens` (uncached = input - cached).
  * A `compacted` record starts a new context window. The compaction request
    has its own usage record, so it is counted. If the cumulative counter ever
    goes DOWN (a reset), the per-request value is trusted and the reset is
    counted in `checks.cumulative_resets` rather than producing a negative
    delta.

Operator metrics: input / cached / output / uncached, deduped request count,
per-turn totals (`turn_id`), compactions, and the POLL count: requests whose
preceding tool calls were all waiting/polling on a specialist (sleep/wait,
an empty write_stdin, or peeking at a specialist transcript). Each poll
re-sends the whole carried context, which is why it is counted separately.

Specialist metrics: tokens by role and round. Round N of a role is that
role's Nth dispatch in time order.
  * Claude Code specialists: the benchmark's `claude` shim records
    `{"harness": "claude", "role": <agent>, "session_id": <uuid>}` in a ledger
    and pins `--session-id`, so the transcript `<uuid>.jsonl` maps to a role
    exactly. Transcript usage repeats per content block, so it is deduped by
    `message.id`.
  * Codex specialists: every non-operator rollout under the isolated
    CODEX_HOME. The role comes from dispatch_agent.py's fixed prompt prefix
    ("Act as the mARC <agent> specialist"); anything else is `codex-other`.

Usage:
  codex_rollout_tokens.py rollout ROLLOUT.jsonl
  codex_rollout_tokens.py summarize --events EVENTS.jsonl --codex-home DIR \
      [--ledger LEDGER.jsonl] [--claude-projects DIR] [--label TEXT]
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../core/scripts"))
from token_sentinel import weighted_usage_tokens  # noqa: E402

FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")

BUCKETS = (
    "dispatch",            # dispatch_agent.py invocations
    "read_specialist_output",
    "poll",                # sleep/wait/empty write_stdin/peeking at a specialist transcript
    "read_instructions",   # skill/agent/plugin self-discovery
    "git_gh_ci",
    "other_tool",
    "user_turn_start",     # request right after a user message
    "model_continuation",  # request with no tool output in between
    "compaction",
)

ROLE_RE = re.compile(r"Act as the mARC ([A-Za-z0-9_-]+) specialist")


def load_jsonl(path: str) -> list[dict]:
    rows: list[dict] = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def _usage(d) -> dict:
    d = d if isinstance(d, dict) else {}
    out = {}
    for f in FIELDS:
        try:
            out[f] = int(d.get(f, 0) or 0)
        except (TypeError, ValueError):
            out[f] = 0
    return out


_WRAP_RE = re.compile(r"""^\s*(?:/\S*/)?(?:ba|z)?sh\s+-l?c\s+(['"])(.*)\1\s*$""", re.S)
_POLL_SEGMENT_RE = re.compile(r"^(sleep\s+\d+(\.\d+)?[smh]?|wait|true|:)$")


def _shell_command(raw) -> str | None:
    """The shell command a tool call runs, unwrapped from `bash -lc '...'`.
    Handles a raw command string, a JSON `{"cmd": ...}` / `{"command": ...}`
    arguments object, and an argv list. None when it isn't a shell call."""
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = raw
        raw = parsed
    if isinstance(raw, dict):
        raw = raw.get("cmd", raw.get("command"))
    if isinstance(raw, list):
        items = [str(x) for x in raw]
        if len(items) >= 3 and os.path.basename(items[0]) in ("bash", "sh", "zsh") and items[1] in ("-c", "-lc"):
            return items[2].strip()
        return " ".join(items).strip()
    if not isinstance(raw, str):
        return None
    m = _WRAP_RE.match(raw)
    return (m.group(2) if m else raw).strip()


def is_pure_wait(cmd: str | None) -> bool:
    """True only when every segment of the command is a sleep/wait (REV-3,
    PR #347): `pytest; sleep 1` did real work and is not a poll."""
    if not cmd:
        return False
    segs = [x.strip() for x in re.split(r"&&|\|\||;|\n", cmd)]
    segs = [x for x in segs if x]
    return bool(segs) and all(_POLL_SEGMENT_RE.match(x) for x in segs)


def classify_call(p: dict) -> str:
    """Bucket for one tool call. Text is lowercased and path-normalized."""
    name = str(p.get("name") or "")
    raw = p.get("input")
    if raw is None:
        raw = p.get("arguments")
    text = raw if isinstance(raw, str) else json.dumps(raw or "")
    if p.get("type") == "function_call" and name in ("sleep", "wait", "wait_agent"):
        return "poll"
    if name == "write_stdin" or ("write_stdin" in text and "exec_command" not in text):
        if not re.search(r'"chars"\s*:\s*"[^"]', text):
            return "poll"
    t = text.lower().replace("\\\\", "/").replace("\\", "/")
    if "dispatch_agent.py" in t and "--help" not in t:
        return "dispatch"
    if is_pure_wait(_shell_command(raw)) or re.search(r"\bgh\s+(run|pr)\s+(watch|checks\b.*--watch)", t):
        return "poll"
    if ".claude/projects" in t or ("/sessions/" in t and "rollout-" in t):
        return "poll"
    if re.search(r"-result\.json|-review\.md", t):
        return "read_specialist_output"
    if re.search(r"skill\.md|/agents/[^ ]+\.(md|toml)|plugins/cache/", t):
        return "read_instructions"
    if re.search(r"(^|[^a-z])(git|gh)\s", t):
        return "git_gh_ci"
    return "other_tool"


def _requests(rows: list[dict], checks: dict) -> list[dict]:
    """Deduped per-request usage, in rollout order, with tool-call attribution."""
    has_records = any(o.get("type") == "token_usage_record" for o in rows)
    checks["source"] = "token_usage_record" if has_records else "token_count"
    compaction_rids = {(o.get("payload") or {}).get("compaction_response_id")
                       for o in rows if o.get("type") == "compacted"}
    compaction_rids.discard(None)

    reqs: list[dict] = []
    seen: set = set()
    pending: list[str] = []
    after_user = False
    window = 0
    turn = None
    prev_total = None
    call_bucket: dict = {}
    compaction_pending = False

    def charge(usage: dict, rid=None, turn_id=None) -> None:
        nonlocal pending, after_user, compaction_pending
        if (rid is not None and rid in compaction_rids) or compaction_pending:
            share = {"compaction": 1.0}
            compaction_pending = False
        elif pending:
            share = defaultdict(float)
            for b in pending:
                share[b] += 1.0 / len(pending)
            share = dict(share)
        else:
            share = {"user_turn_start" if after_user else "model_continuation": 1.0}
        reqs.append({"usage": usage, "share": share, "window": window,
                     "turn": turn_id or turn, "poll": bool(pending) and all(b == "poll" for b in pending)})
        pending, after_user = [], False

    for o in rows:
        typ = o.get("type")
        p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        if typ == "compacted":
            window += 1
            checks["compactions"] += 1
            # Records mode matches the compaction request by id; a compacted
            # record without one (or the token_count fallback, which has no
            # ids) charges the next request instead (REV-4, PR #347).
            if not has_records or not p.get("compaction_response_id"):
                compaction_pending = True
            continue
        if typ == "turn_context" or (typ == "event_msg" and p.get("type") == "task_started"):
            turn = p.get("turn_id") or turn
            continue
        if typ == "response_item":
            pt = p.get("type")
            if pt == "message" and p.get("role") == "user":
                pending, after_user = [], True
            elif pt in ("custom_tool_call", "function_call", "local_shell_call"):
                call_bucket[p.get("call_id")] = classify_call(p)
            elif pt in ("custom_tool_call_output", "function_call_output"):
                pending.append(call_bucket.get(p.get("call_id"), "other_tool"))
            continue
        if typ == "token_usage_record" and has_records:
            checks["usage_records"] += 1
            rid = p.get("response_id")
            # No response_id: dedupe on (turn, cumulative counter) instead, so
            # a re-emitted id-less record is still counted once (REV-4).
            key = ("rid", rid) if rid is not None else (
                "turn", p.get("turn_id"), json.dumps(p.get("thread_token_usage"), sort_keys=True))
            if key in seen:
                checks["duplicates_skipped"] += 1
                continue
            seen.add(key)
            usage = _usage(p.get("usage"))
            total = _usage(p.get("thread_token_usage"))
            if prev_total is not None and any(total[f] < prev_total[f] for f in FIELDS):
                checks["cumulative_resets"] += 1
            elif prev_total is not None and any(total[f] - prev_total[f] != usage[f] for f in FIELDS):
                checks["cumulative_mismatch"] += 1
            prev_total = total
            charge(usage, rid=rid, turn_id=p.get("turn_id"))
            continue
        if typ == "event_msg" and p.get("type") == "token_count":
            checks["token_count_events"] += 1
            if has_records:
                continue  # cross-check only; records are authoritative
            info = p.get("info") if isinstance(p.get("info"), dict) else None
            if not info:
                checks["duplicates_skipped"] += 1
                continue
            total = _usage(info.get("total_token_usage"))
            last = _usage(info.get("last_token_usage"))
            if prev_total is not None and total == prev_total:
                checks["duplicates_skipped"] += 1
                continue
            if prev_total is not None and any(total[f] < prev_total[f] for f in FIELDS):
                checks["cumulative_resets"] += 1
                usage = last
            elif prev_total is not None:
                usage = {f: total[f] - prev_total[f] for f in FIELDS}
                if usage != last:
                    checks["cumulative_mismatch"] += 1
            else:
                usage = last if any(last.values()) else total
            prev_total = total
            charge(usage)
    return reqs


def parse_rollout(path: str) -> dict:
    rows = load_jsonl(path)
    checks = {"usage_records": 0, "token_count_events": 0, "duplicates_skipped": 0,
              "cumulative_mismatch": 0, "cumulative_resets": 0, "compactions": 0}
    reqs = _requests(rows, checks)

    totals = {f: 0 for f in FIELDS}
    by_bucket = {b: {"requests": 0.0, "input_tokens": 0.0} for b in BUCKETS}
    per_turn: dict = {}
    for r in reqs:
        for f in FIELDS:
            totals[f] += r["usage"][f]
        for b, s in r["share"].items():
            by_bucket[b]["requests"] += s
            by_bucket[b]["input_tokens"] += r["usage"]["input_tokens"] * s
        key = r["turn"] or "unknown"
        t = per_turn.setdefault(key, {f: 0 for f in FIELDS} | {"requests": 0})
        for f in FIELDS:
            t[f] += r["usage"][f]
        t["requests"] += 1

    # Carried history: input added by request n (vs n-1, same compaction
    # window) is re-sent by every later request in that window.
    carried = {b: 0.0 for b in BUCKETS + ("window_start",)}
    for i, r in enumerate(reqs):
        later = sum(1 for x in reqs[i:] if x["window"] == r["window"])
        prev = reqs[i - 1] if i and reqs[i - 1]["window"] == r["window"] else None
        if prev is None:
            carried["window_start"] += r["usage"]["input_tokens"] * later
            continue
        delta = r["usage"]["input_tokens"] - prev["usage"]["input_tokens"]
        for b, s in r["share"].items():
            carried[b] += delta * s * later

    meta = next((o.get("payload") for o in rows if o.get("type") == "session_meta"), None) or {}
    first_user = ""
    for o in rows:
        p = o.get("payload") if isinstance(o.get("payload"), dict) else {}
        if o.get("type") == "response_item" and p.get("type") == "message" and p.get("role") == "user":
            first_user += json.dumps(p.get("content", ""))
            if ROLE_RE.search(first_user):
                break
    m = ROLE_RE.search(first_user)
    return {
        "thread_id": meta.get("id") or meta.get("session_id"),
        "first_ts": meta.get("timestamp") or (rows[0].get("timestamp") if rows else None),
        "role": m.group(1) if m else None,
        "checks": checks,
        "requests": len(reqs),
        "turns": len(per_turn),
        "poll_turns": sum(1 for r in reqs if r["poll"]),
        "compactions": checks["compactions"],
        "input_tokens": totals["input_tokens"],
        "cached_input_tokens": totals["cached_input_tokens"],
        "uncached_input_tokens": totals["input_tokens"] - totals["cached_input_tokens"],
        "output_tokens": totals["output_tokens"],
        "reasoning_output_tokens": totals["reasoning_output_tokens"],
        "weighted": codex_weighted(totals),
        "per_turn": per_turn,
        "by_bucket": {b: {k: round(v) for k, v in d.items()} for b, d in by_bucket.items() if d["requests"]},
        "carried_input_by_bucket": {b: round(v) for b, v in carried.items() if v},
    }


def codex_weighted(t: dict) -> int:
    """Same cost weighting as the Claude arm (token_sentinel): fresh tokens at
    full rate, cache reads discounted. Output is fresh."""
    cached = t.get("cached_input_tokens", 0)
    fresh = t.get("input_tokens", 0) - cached + t.get("output_tokens", 0)
    return weighted_usage_tokens(fresh, cached)


def parse_claude_transcript(path: str) -> dict:
    """One Claude Code transcript; usage repeats per content block -> dedupe
    by message.id (last write wins)."""
    msgs: dict = {}
    first_ts = None
    for o in load_jsonl(path):
        first_ts = first_ts or o.get("timestamp")
        m = o.get("message") if isinstance(o.get("message"), dict) else {}
        if o.get("type") == "assistant" and isinstance(m.get("usage"), dict) and m.get("id"):
            msgs[m["id"]] = m["usage"]
    tot = {"input_uncached": 0, "cache_creation": 0, "cache_read": 0, "output": 0}
    for u in msgs.values():
        tot["input_uncached"] += int(u.get("input_tokens", 0) or 0)
        tot["cache_creation"] += int(u.get("cache_creation_input_tokens", 0) or 0)
        tot["cache_read"] += int(u.get("cache_read_input_tokens", 0) or 0)
        tot["output"] += int(u.get("output_tokens", 0) or 0)
    fresh = tot["input_uncached"] + tot["cache_creation"] + tot["output"]
    return {"messages": len(msgs), "first_ts": first_ts, **tot,
            "total": fresh + tot["cache_read"], "weighted": weighted_usage_tokens(fresh, tot["cache_read"])}


def _events_thread_id(events_path: str) -> str | None:
    for o in load_jsonl(events_path):
        if o.get("type") == "thread.started" and o.get("thread_id"):
            return str(o["thread_id"])
    return None


def _rollout_id(path: str) -> str | None:
    """The thread id from a rollout's session_meta (not the filename: one
    id can be a suffix of another)."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if i > 50:
                break
            try:
                o = json.loads(line)
            except ValueError:
                continue
            if isinstance(o, dict) and o.get("type") == "session_meta" and isinstance(o.get("payload"), dict):
                p = o["payload"]
                return str(p.get("id") or p.get("session_id") or "") or None
    return None


def summarize(events: str, codex_home: str, ledger: str | None, claude_projects: str | None,
              label: str | None = None) -> dict:
    rollouts = sorted(glob.glob(os.path.join(codex_home, "sessions", "**", "rollout-*.jsonl"), recursive=True))
    thread_id = _events_thread_id(events) if events and os.path.isfile(events) else None
    if thread_id:
        op_path = next((r for r in rollouts if _rollout_id(r) == thread_id), None)
    else:
        # No thread.started event: the operator is the earliest rollout.
        op_path = min(rollouts, key=lambda r: os.path.basename(r)) if rollouts else None
    if not op_path:
        raise SystemExit(f"no operator rollout found under {codex_home}/sessions (thread_id={thread_id})")
    operator = parse_rollout(op_path)
    operator.pop("per_turn", None)

    specialists: list[dict] = []
    for r in rollouts:
        if r == op_path:
            continue
        s = parse_rollout(r)
        specialists.append({"harness": "codex", "role": s["role"] or "codex-other", "first_ts": s["first_ts"] or "",
                            "total": s["input_tokens"] + s["output_tokens"], "weighted": s["weighted"]})
    missing = 0
    if ledger and os.path.isfile(ledger):
        for e in load_jsonl(ledger):
            if e.get("harness") != "claude" or not e.get("session_id"):
                continue
            hits = glob.glob(os.path.join(claude_projects or "", "*", f"{e['session_id']}.jsonl"))
            if not hits:
                missing += 1
                continue
            c = parse_claude_transcript(hits[0])
            specialists.append({"harness": "claude", "role": str(e.get("role") or "claude-other"),
                                "first_ts": str(e.get("ts") or c["first_ts"] or ""),
                                "total": c["total"], "weighted": c["weighted"]})

    by_role: dict = defaultdict(dict)
    counts: dict = defaultdict(int)
    for s in sorted(specialists, key=lambda s: s["first_ts"]):
        counts[s["role"]] += 1
        by_role[s["role"]][f"round{counts[s['role']]}"] = s["weighted"]
    spec_weighted = sum(s["weighted"] for s in specialists)
    return {
        "label": label,
        "operator": operator,
        "specialists_by_role_round": dict(by_role),
        "specialist_invocations": len(specialists),
        "specialist_transcripts_missing": missing,
        "specialist_weighted": spec_weighted,
        # `weighted` is what benchmark_report.median_weighted() reads.
        "weighted": operator["weighted"] + spec_weighted,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("rollout", help="parse one rollout")
    r.add_argument("path")
    s = sub.add_parser("summarize", help="one benchmark-run summary record")
    s.add_argument("--events", default="", help="`codex exec --json` stdout of the operator run")
    s.add_argument("--codex-home", required=True)
    s.add_argument("--ledger", default=None)
    s.add_argument("--claude-projects", default=None)
    s.add_argument("--label", default=None)
    a = ap.parse_args(argv)
    if a.cmd == "rollout":
        res = parse_rollout(a.path)
        json.dump(res, sys.stdout, indent=1)
        print()
    else:
        print(json.dumps(summarize(a.events, a.codex_home, a.ledger, a.claude_projects, a.label), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
