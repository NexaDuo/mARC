#!/usr/bin/env python3
"""Opt-in, default-OFF per-turn token-cost telemetry recorder (origin: #149).

Motivating research brief: issue #148. Claude Code's hook stdin JSON never
carries usage/cost data for any event (measured fact from #148) — the only
documented path to per-turn token counts is parsing `transcript_path` (the
session `.jsonl`). This module reuses `token_sentinel.analyze()` verbatim
(same folding logic the PostToolUse token-guard already depends on, #71/#73/
#81/#100) instead of reinventing transcript parsing.

PRIVACY BOUNDARY (explicit, non-negotiable): only NUMERIC usage metadata is
ever stored — timestamps, token counts, a model name, a session id, a turn
index, and a repo/cwd BASENAME (not a full path). The prompt/response TEXT
(`prompt`, message content) is NEVER written to the telemetry file, even
though `token_sentinel.analyze()` computes a `prompt` field internally for
its own manual-CLI/report display — this module deliberately drops that
field before persisting a record.

OPT-IN, DEFAULT OFF: nothing is written unless the CONSUMING repo's
`.claude/team.toml` has `[telemetry]` / `enabled = true`. Any other state
(missing team.toml, missing section, `enabled = false`, malformed value) is
treated as disabled — silent no-op, matching the plugin-wide warn-only/exit-0
hook contract. This module never raises out of its own hook entrypoint.

Storage: append-only JSONL at `$MARC_STATE_DIR/token-telemetry.jsonl`
(defaults to `~/.claude/marc-state/`, same convention as #52's
outdated-check state file) — one line per turn, schema:

    ts            unix epoch timestamp (float) of the turn's last assistant
                  message ("" in the transcript -> falls back to now())
    session_id    Claude Code session id (from the hook payload)
    turn_index    0-based turn number within the session
    model         last model seen in the turn ("-" if none)
    input         raw `input_tokens` summed over the turn
    output        raw `output_tokens` summed over the turn
    cache_read    raw `cache_read_input_tokens` summed over the turn
    cache_write   raw `cache_creation_input_tokens` summed over the turn
    weighted      cost-weighted total (`token_sentinel.weighted_usage_tokens`,
                  origin: #100 — fresh full-rate, cache-read discounted)
    repo          basename of the session's cwd (coarse context only, never
                  a full path)

Two entrypoints:
  * `--hook` (Stop event, Claude Code only): reads the hook stdin JSON,
    ALWAYS exits 0, writes at most one line (the just-completed turn).
  * Library functions (`is_enabled`, `build_record`, `append_record`) are
    reused as-is by `token_telemetry_backfill.py` and
    `token_telemetry_report.py` so all three tools agree on one schema.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_sentinel import analyze  # noqa: E402

STATE_DIR_ENV = "MARC_STATE_DIR"
TELEMETRY_FILENAME = "token-telemetry.jsonl"

# Same zero-dependency key-anchored extraction convention as
# `board.py`'s `toml_get` / the shell `sed` pattern used elsewhere in the
# plugin (docs/team.toml.example schema contract) — key names are unique
# file-wide by convention (CI-enforced), so a single regex is safe without a
# TOML parser dependency.
_TOML_KEY_RE_TEMPLATE = r'^[ \t]*{key}[ \t]*=[ \t]*"?([^"#\n]*)"?'


def toml_get(text: str, key: str) -> str | None:
    pattern = re.compile(_TOML_KEY_RE_TEMPLATE.format(key=re.escape(key)), re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


def is_enabled(team_toml_text: str) -> bool:
    """Opt-in toggle: only the literal string "true" enables writes. Missing
    key, missing section, "false", or any other value -> disabled."""
    return (toml_get(team_toml_text, "enabled") or "").strip().lower() == "true"


def telemetry_enabled_for_project(project_dir: str) -> bool:
    """True only if `<project_dir>/.claude/team.toml` exists and has
    `[telemetry] enabled = true`. Any failure (missing file, unreadable,
    malformed) -> disabled (fail-closed for an opt-in feature)."""
    path = os.path.join(project_dir, ".claude", "team.toml")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    return is_enabled(text)


def state_dir() -> str:
    return os.environ.get(STATE_DIR_ENV) or os.path.join(
        os.path.expanduser("~"), ".claude", "marc-state"
    )


def telemetry_path() -> str:
    return os.path.join(state_dir(), TELEMETRY_FILENAME)


def _epoch_from_iso(ts: str) -> float:
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return time.time()


def build_record(turn: dict, *, session_id: str, turn_index: int, repo: str) -> dict:
    """Build ONE schema-compliant record from a `token_sentinel.analyze()`
    turn dict. Deliberately whitelists fields — never forwards the turn's
    `prompt` (message-body-derived) or any other unlisted key, so a future
    additive field on the shared turn dict can never leak into telemetry
    without an explicit code change here (privacy boundary, #149)."""
    last_ts = turn.get("last_ts") or ""
    return {
        "ts": _epoch_from_iso(last_ts) if last_ts else time.time(),
        "session_id": session_id,
        "turn_index": turn_index,
        "model": turn.get("model", "-"),
        "input": int(turn.get("input_tokens", 0) or 0),
        "output": int(turn.get("output_tokens", 0) or 0),
        "cache_read": int(turn.get("cache_read_tokens", 0) or 0),
        "cache_write": int(turn.get("cache_write_tokens", 0) or 0),
        "weighted": int(turn.get("tokens", 0) or 0),
        "repo": repo,
    }


def append_record(record: dict, *, path: str | None = None) -> bool:
    """Append ONE JSON line. Best-effort: any I/O failure is swallowed and
    returns False (warn-only contract — telemetry must never break a turn)."""
    target = path or telemetry_path()
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(target, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, sort_keys=True))
            fh.write("\n")
        return True
    except OSError:
        return False


def repo_basename(cwd: str | None) -> str:
    if not cwd:
        return "-"
    return os.path.basename(os.path.abspath(cwd)) or "-"


def run_hook(stdin_text: str) -> int:
    """Warn-only Stop-hook entrypoint. ALWAYS returns 0; never raises out.

    Cheap by construction: a single `analyze()` pass over the transcript
    (same parse the PostToolUse token-guard already pays for every tool
    call, #71) run ONCE per turn (Stop fires once per turn, not once per
    tool call) and at most one JSONL line appended.
    """
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
        return 0

    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    if not telemetry_enabled_for_project(cwd):
        return 0

    transcript_path = payload.get("transcript_path")
    if not transcript_path or not os.path.isfile(transcript_path):
        return 0

    try:
        turns = analyze(transcript_path)
    except OSError:
        return 0
    if not turns:
        return 0

    turn_index = len(turns) - 1  # current (just-completed) turn
    turn = turns[turn_index]
    session_id = str(payload.get("session_id") or "-")
    record = build_record(turn, session_id=session_id, turn_index=turn_index,
                           repo=repo_basename(cwd))
    append_record(record)
    return 0


def main(argv=None) -> int:
    import argparse

    ap = argparse.ArgumentParser(
        description="Opt-in per-turn token-cost telemetry recorder (Stop hook, origin: #149)."
    )
    ap.add_argument("--hook", action="store_true",
                     help="run as a warn-only Stop hook (reads hook JSON on stdin; always exits 0)")
    args = ap.parse_args(argv)

    if args.hook:
        try:
            return run_hook(sys.stdin.read())
        except Exception:  # noqa: BLE001 — a hook must never break a turn.
            return 0

    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
