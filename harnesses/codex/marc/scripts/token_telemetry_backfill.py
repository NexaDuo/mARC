#!/usr/bin/env python3
"""One-shot, manual/opt-in backfill of `token-telemetry.jsonl` from existing
Claude Code session transcripts (origin: #149, brief: #148).

NEVER auto-run: this script is a standalone CLI the operator invokes by hand
(no hook wires it up). It mines every session `.jsonl` under
`~/.claude/projects/**/*.jsonl` with `token_sentinel.analyze()` (the SAME
parse the Stop-hook recorder and the PostToolUse guard already use) and
appends one record per turn to the SAME telemetry file the recorder writes,
using `token_telemetry.build_record()` so both paths share one schema.

RETENTION CEILING (must-read before running): Claude Code deletes session
transcripts older than `cleanupPeriodDays` (default 30 days) on startup —
silently and irreversibly (confirmed on code.claude.com/docs/en/claude-
directory, #148 brief). This backfill can only recover turns from transcripts
still on disk; anything already rotated off is permanently gone and this
script cannot invent it. Run it as soon as telemetry is turned on if you want
the fullest possible history — waiting loses more of the tail every day.

Historical pricing is NOT reconstructed: the recorded `weighted`/token counts
are raw counts folded the same way the live recorder folds them, not a
dollar figure — there is no maintained historical price table in this repo,
and Anthropic publishes no price-change changelog (#148 brief, unconfirmed
gap). Cost-in-dollars is left to the report script / a future price table,
never invented here.

IDEMPOTENT: keyed on `(session_id, turn_index)`. Re-running after new
sessions have appeared (or after lowering `MARC_STATE_DIR`) only appends
records for turns not already present in the target file — it never
duplicates a line, so it is safe to run repeatedly (e.g. as a periodic
manual top-up) without growing the file unboundedly.

Usage:
    python3 token_telemetry_backfill.py [--dry-run]
        --dry-run   print what WOULD be appended; write nothing.

Honors MARC_STATE_DIR (same as the recorder) for the target file location.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from token_sentinel import analyze  # noqa: E402
from token_telemetry import (  # noqa: E402
    build_record,
    repo_basename,
    telemetry_path,
)


def existing_keys(path: str) -> set[tuple[str, int]]:
    """Read the target file's existing (session_id, turn_index) keys so a
    re-run never duplicates a line. Missing/unreadable file -> empty set
    (first run)."""
    keys: set[tuple[str, int]] = set()
    if not os.path.isfile(path):
        return keys
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                sid = rec.get("session_id")
                idx = rec.get("turn_index")
                if sid is not None and isinstance(idx, int):
                    keys.add((sid, idx))
    except OSError:
        pass
    return keys


def find_transcripts(projects_root: str) -> list[str]:
    return sorted(glob.glob(os.path.join(projects_root, "*", "*.jsonl")))


def backfill(projects_root: str, target_path: str, *, dry_run: bool = False) -> tuple[int, int]:
    """Returns (records_written, sessions_scanned)."""
    seen = existing_keys(target_path)
    written = 0
    sessions = 0

    for transcript in find_transcripts(projects_root):
        session_id = os.path.splitext(os.path.basename(transcript))[0]
        try:
            turns = analyze(transcript)
        except OSError:
            continue
        if not turns:
            continue
        sessions += 1
        # Coarse repo context: the transcript's PARENT directory name is
        # Claude Code's encoded project-dir, not a human path — fall back to
        # that encoded name rather than trying to decode it back to a real
        # path (decoding is lossy: '-' replaced every non-alphanumeric char).
        repo = repo_basename(os.path.dirname(transcript))
        new_lines = []
        for turn_index, turn in enumerate(turns):
            key = (session_id, turn_index)
            if key in seen:
                continue
            record = build_record(turn, session_id=session_id,
                                   turn_index=turn_index, repo=repo)
            new_lines.append(record)
            seen.add(key)

        if not new_lines:
            continue
        if dry_run:
            written += len(new_lines)
            continue
        os.makedirs(os.path.dirname(target_path), exist_ok=True)
        with open(target_path, "a", encoding="utf-8") as fh:
            for record in new_lines:
                fh.write(json.dumps(record, sort_keys=True))
                fh.write("\n")
        written += len(new_lines)

    return written, sessions


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true",
                     help="print counts without writing anything")
    ap.add_argument("--projects-root", default=None,
                     help="override ~/.claude/projects (for testing)")
    args = ap.parse_args(argv)

    projects_root = args.projects_root or os.path.join(
        os.path.expanduser("~"), ".claude", "projects"
    )
    target_path = telemetry_path()

    if not os.path.isdir(projects_root):
        print(f"no Claude Code project transcripts found under {projects_root}")
        return 0

    written, sessions = backfill(projects_root, target_path, dry_run=args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    print(f"token-telemetry backfill: scanned {sessions} session transcript(s) "
          f"under {projects_root}, {verb} {written} new turn record(s) to "
          f"{target_path}.")
    print("Reminder: transcripts older than ~30 days (default cleanupPeriodDays) "
          "are already gone and cannot be recovered; historical pricing is not "
          "reconstructed, only raw token counts.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
