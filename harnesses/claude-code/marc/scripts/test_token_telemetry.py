#!/usr/bin/env python3
"""Self-test for the opt-in token-telemetry recorder (origin: #149).

Deterministic, offline, zero token cost. Synthesizes fake transcript/team.toml
fixtures (no real paths/names/session ids) and asserts:
  * the toggle is OFF by default (missing team.toml, missing section,
    `enabled = false`, malformed value) -> no telemetry file is created at all.
  * `enabled = true` -> the Stop hook writes EXACTLY one well-formed JSONL
    line for the completed turn, with the whitelisted numeric-only schema
    (no `prompt`/message-body field ever leaks through).
  * a missing/unreadable transcript -> exit 0, no write, even when enabled.
  * the hook always exits 0 (warn-only contract) regardless of outcome.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import token_telemetry as tt  # noqa: E402

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    status = "ok" if cond else "FAIL"
    print(f"[{status}] {msg}")
    if not cond:
        _failures.append(msg)


def write_transcript(path: str, turns: list[dict]) -> None:
    """Each turn dict: {"model": ..., "input": N, "output": N, "cache_read": N,
    "cache_write": N}. Writes one user record + one assistant record per turn."""
    with open(path, "w", encoding="utf-8") as fh:
        for i, t in enumerate(turns):
            user_rec = {"type": "user", "message": {"content": f"turn {i} prompt text"}}
            fh.write(json.dumps(user_rec) + "\n")
            assistant_rec = {
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
            }
            fh.write(json.dumps(assistant_rec) + "\n")


def write_team_toml(path: str, telemetry_block: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[github]\ngh_org = \"Test\"\ngh_repo = \"Test/repo\"\n")
        fh.write(telemetry_block)


def run_hook_subprocess(*, project_dir: str, plugin_root: str, state_dir: str,
                         transcript_path: str, session_id: str = "sess-1") -> tuple[int, str]:
    hook = os.path.join(plugin_root, "hooks", "token-telemetry.sh")
    payload = json.dumps({
        "session_id": session_id,
        "cwd": project_dir,
        "transcript_path": transcript_path,
        "hook_event_name": "Stop",
    })
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = project_dir
    env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    env["MARC_STATE_DIR"] = state_dir
    proc = subprocess.run(["bash", hook], input=payload, capture_output=True,
                           text=True, env=env, timeout=15)
    return proc.returncode, proc.stdout


def setup_plugin_root(base: str) -> str:
    """Stage a minimal CLAUDE_PLUGIN_ROOT with the real hook + real scripts
    (symlinked-in-spirit via copy, so the test exercises the actual shipped
    files, not a reimplementation)."""
    plugin_root = os.path.join(base, "plugin")
    os.makedirs(os.path.join(plugin_root, "hooks"), exist_ok=True)
    os.makedirs(os.path.join(plugin_root, "scripts"), exist_ok=True)
    repo_root = os.path.dirname(os.path.dirname(HERE))
    hook_src = os.path.join(repo_root, "harnesses", "claude-code", "marc", "hooks",
                             "token-telemetry.sh")
    with open(hook_src, "r", encoding="utf-8") as fh:
        hook_text = fh.read()
    with open(os.path.join(plugin_root, "hooks", "token-telemetry.sh"), "w",
              encoding="utf-8") as fh:
        fh.write(hook_text)
    for name in ("token_telemetry.py", "token_sentinel.py"):
        with open(os.path.join(HERE, name), "r", encoding="utf-8") as fh:
            content = fh.read()
        with open(os.path.join(plugin_root, "scripts", name), "w", encoding="utf-8") as fh:
            fh.write(content)
    return plugin_root


def main() -> int:
    # --- Unit-level: toggle parsing -----------------------------------------
    check(tt.is_enabled("[telemetry]\nenabled = true\n") is True,
          "is_enabled: 'enabled = true' -> True")
    check(tt.is_enabled("[telemetry]\nenabled = false\n") is False,
          "is_enabled: 'enabled = false' -> False")
    check(tt.is_enabled("[telemetry]\n") is False,
          "is_enabled: missing key -> False")
    check(tt.is_enabled("") is False,
          "is_enabled: empty file -> False")
    check(tt.is_enabled("[telemetry]\nenabled = TRUE\n") is True,
          "is_enabled: value comparison is case-insensitive ('TRUE' also enables)")
    check(tt.is_enabled("[telemetry]\nenabled = yes\n") is False,
          "is_enabled: only 'true' (any case) enables, not 'yes'/other truthy strings")

    # --- Unit-level: record schema whitelist --------------------------------
    turn = {"model": "claude-opus-4-8-20260101", "input_tokens": 100,
            "output_tokens": 50, "cache_read_tokens": 200, "cache_write_tokens": 10,
            "tokens": 121, "last_ts": "2026-07-21T00:00:00.000Z",
            "prompt": "this is message-body text that must NEVER leak"}
    record = tt.build_record(turn, session_id="sess-abc", turn_index=2, repo="myrepo")
    check("prompt" not in record, "build_record: never forwards the 'prompt' (message-body) field")
    expected_keys = {"ts", "session_id", "turn_index", "model", "input", "output",
                     "cache_read", "cache_write", "weighted", "repo"}
    check(set(record.keys()) == expected_keys,
          f"build_record: schema is exactly {sorted(expected_keys)}, got {sorted(record.keys())}")
    check(record["input"] == 100 and record["output"] == 50
          and record["cache_read"] == 200 and record["cache_write"] == 10
          and record["weighted"] == 121 and record["session_id"] == "sess-abc"
          and record["turn_index"] == 2 and record["repo"] == "myrepo",
          "build_record: field values map correctly from the analyze() turn dict")

    # --- End-to-end: OFF by default (no team.toml at all) -------------------
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "proj-off1")
        os.makedirs(project, exist_ok=True)
        state = os.path.join(base, "state-off1")
        plugin_root = setup_plugin_root(base)
        transcript = os.path.join(base, "session.jsonl")
        write_transcript(transcript, [{"model": "claude-sonnet-4-5", "input": 10,
                                        "output": 5, "cache_read": 0, "cache_write": 0}])
        rc, _ = run_hook_subprocess(project_dir=project, plugin_root=plugin_root,
                                     state_dir=state, transcript_path=transcript)
        check(rc == 0, "OFF (no team.toml): hook exits 0")
        check(not os.path.exists(os.path.join(state, "token-telemetry.jsonl")),
              "OFF (no team.toml): no telemetry file written")

    # --- End-to-end: OFF (enabled = false explicitly) ------------------------
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "proj-off2")
        write_team_toml(os.path.join(project, ".claude", "team.toml"),
                         "\n[telemetry]\nenabled = false\n")
        state = os.path.join(base, "state-off2")
        plugin_root = setup_plugin_root(base)
        transcript = os.path.join(base, "session.jsonl")
        write_transcript(transcript, [{"model": "claude-sonnet-4-5", "input": 10,
                                        "output": 5, "cache_read": 0, "cache_write": 0}])
        rc, _ = run_hook_subprocess(project_dir=project, plugin_root=plugin_root,
                                     state_dir=state, transcript_path=transcript)
        check(rc == 0, "OFF (enabled = false): hook exits 0")
        check(not os.path.exists(os.path.join(state, "token-telemetry.jsonl")),
              "OFF (enabled = false): no telemetry file written")

    # --- End-to-end: ON -> exactly one well-formed JSONL line ---------------
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "proj-on")
        write_team_toml(os.path.join(project, ".claude", "team.toml"),
                         "\n[telemetry]\nenabled = true\n")
        state = os.path.join(base, "state-on")
        plugin_root = setup_plugin_root(base)
        transcript = os.path.join(base, "session.jsonl")
        write_transcript(transcript, [
            {"model": "claude-opus-4-8-20260101", "input": 1000, "output": 200,
             "cache_read": 500, "cache_write": 0},
            {"model": "claude-opus-4-8-20260101", "input": 300, "output": 100,
             "cache_read": 900, "cache_write": 50},
        ])
        rc, _ = run_hook_subprocess(project_dir=project, plugin_root=plugin_root,
                                     state_dir=state, transcript_path=transcript,
                                     session_id="sess-on-1")
        telemetry_file = os.path.join(state, "token-telemetry.jsonl")
        check(rc == 0, "ON: hook exits 0")
        check(os.path.isfile(telemetry_file), "ON: telemetry file created")
        if os.path.isfile(telemetry_file):
            with open(telemetry_file, "r", encoding="utf-8") as fh:
                lines = [ln for ln in fh.read().splitlines() if ln.strip()]
            check(len(lines) == 1, f"ON: exactly one JSONL line written per Stop firing, got {len(lines)}")
            if lines:
                rec = json.loads(lines[0])  # must parse as valid JSON
                check(set(rec.keys()) == expected_keys,
                      f"ON: written record has the exact schema, got {sorted(rec.keys())}")
                check(rec["session_id"] == "sess-on-1", "ON: session_id round-trips")
                check(rec["turn_index"] == 1, "ON: turn_index is the LAST (just-completed) turn")
                check(rec["input"] == 300 and rec["output"] == 100
                      and rec["cache_read"] == 900 and rec["cache_write"] == 50,
                      "ON: last-turn usage counts round-trip correctly")
                check("prompt" not in rec and "message" not in rec,
                      "ON: no message-body/content field ever written (privacy boundary)")

        # A second Stop firing (simulating turn 3) must APPEND, not overwrite.
        write_transcript(transcript, [
            {"model": "claude-opus-4-8-20260101", "input": 1000, "output": 200,
             "cache_read": 500, "cache_write": 0},
            {"model": "claude-opus-4-8-20260101", "input": 300, "output": 100,
             "cache_read": 900, "cache_write": 50},
            {"model": "claude-opus-4-8-20260101", "input": 10, "output": 10,
             "cache_read": 10, "cache_write": 0},
        ])
        rc2, _ = run_hook_subprocess(project_dir=project, plugin_root=plugin_root,
                                      state_dir=state, transcript_path=transcript,
                                      session_id="sess-on-1")
        check(rc2 == 0, "ON (2nd Stop firing): hook exits 0")
        with open(telemetry_file, "r", encoding="utf-8") as fh:
            lines2 = [ln for ln in fh.read().splitlines() if ln.strip()]
        check(len(lines2) == 2, f"ON: second Stop firing APPENDS a second line, got {len(lines2)}")

    # --- End-to-end: enabled = true but missing transcript -> exit 0, no write
    with tempfile.TemporaryDirectory() as base:
        project = os.path.join(base, "proj-missing")
        write_team_toml(os.path.join(project, ".claude", "team.toml"),
                         "\n[telemetry]\nenabled = true\n")
        state = os.path.join(base, "state-missing")
        plugin_root = setup_plugin_root(base)
        missing_transcript = os.path.join(base, "does-not-exist.jsonl")
        rc, _ = run_hook_subprocess(project_dir=project, plugin_root=plugin_root,
                                     state_dir=state, transcript_path=missing_transcript)
        check(rc == 0, "ON + missing transcript: hook still exits 0")
        check(not os.path.exists(os.path.join(state, "token-telemetry.jsonl")),
              "ON + missing transcript: no telemetry file written")

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll token-telemetry self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
