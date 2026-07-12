#!/usr/bin/env python3
"""Self-test for the warn-only PostToolUse token-guard hook (origin: #71).

Stdlib only (no pytest); run directly:  python3 test_token_sentinel.py

Synthesizes fake transcript fixtures (NO real paths / names / session ids) and
drives `token_sentinel.py --hook` as a subprocess exactly the way Claude Code
would, asserting:

  * the advisory fires ONCE when a turn first crosses the Opus threshold, and is
    debounced (silent) on the next tool call in the same band;
  * it re-arms and fires again at the 2N band;
  * it does NOT fire below the threshold;
  * it does NOT fire for a Sonnet-only turn, however long;
  * the emitted payload is NON-BLOCKING (no `decision`, correct hookEventName);
  * the hook ALWAYS exits 0, including on empty / garbage stdin.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SENTINEL = os.path.join(HERE, "token_sentinel.py")
THRESHOLD = 5  # small, so fixtures stay tiny
OPUS = "claude-opus-4-8-20260101"
SONNET = "claude-sonnet-4-5-20250101"

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def write_transcript(dirpath: str, name: str, model: str, tool_calls: int) -> str:
    """One real user turn followed by `tool_calls` assistant tool-call requests."""
    path = os.path.join(dirpath, name)
    lines = [{"type": "user", "message": {"role": "user", "content": "do a thing"}}]
    for i in range(tool_calls):
        lines.append({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "model": model,
                "content": [{"type": "tool_use", "id": f"t{i}", "name": "Bash", "input": {}}],
                "usage": {"input_tokens": 10, "cache_read_input_tokens": 0,
                          "cache_creation_input_tokens": 0},
            },
        })
    with open(path, "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")
    return path


def run_hook(env_tmp: str, transcript_path: str | None, session_id: str,
             stdin_override: str | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    env["TMPDIR"] = env_tmp                       # isolate the debounce state dir
    env["MARC_TOKEN_GUARD_THRESHOLD"] = str(THRESHOLD)
    if stdin_override is not None:
        stdin_text = stdin_override
    else:
        stdin_text = json.dumps({"transcript_path": transcript_path,
                                 "session_id": session_id})
    proc = subprocess.run(
        [sys.executable, SENTINEL, "--hook"],
        input=stdin_text, capture_output=True, text=True, env=env,
    )
    return proc.returncode, proc.stdout


def parse_advisory(stdout: str) -> dict:
    data = json.loads(stdout)
    check(data.get("hookSpecificOutput", {}).get("hookEventName") == "PostToolUse",
          "advisory hookEventName is PostToolUse")
    check(bool(data.get("hookSpecificOutput", {}).get("additionalContext")),
          "advisory carries non-empty additionalContext")
    check("decision" not in data,
          "advisory has NO top-level 'decision' key (non-blocking)")
    return data


def main() -> int:
    with tempfile.TemporaryDirectory() as work:
        fixtures = os.path.join(work, "fixtures")
        state_tmp = os.path.join(work, "state")
        os.makedirs(fixtures)
        os.makedirs(state_tmp)

        # 1. Below threshold on Opus -> silent.
        below = write_transcript(fixtures, "below.jsonl", OPUS, THRESHOLD - 1)
        rc, out = run_hook(state_tmp, below, "sess-below")
        check(rc == 0, "below-threshold: exit 0")
        check(out.strip() == "", "below-threshold: no advisory emitted")

        # 2. Crossing N -> fires once; same band next call -> debounced silent.
        at_n = write_transcript(fixtures, "at_n.jsonl", OPUS, THRESHOLD)
        rc, out = run_hook(state_tmp, at_n, "sess-cross")
        check(rc == 0, "crossing N: exit 0")
        check(out.strip() != "", "crossing N: advisory fires once")
        if out.strip():
            parse_advisory(out)

        past_n = write_transcript(fixtures, "past_n.jsonl", OPUS, THRESHOLD + 1)
        rc, out = run_hook(state_tmp, past_n, "sess-cross")  # same session -> debounce
        check(rc == 0, "same band (N+1): exit 0")
        check(out.strip() == "", "same band (N+1): debounced, no second advisory")

        # 3. Re-arms and fires again at the 2N band.
        at_2n = write_transcript(fixtures, "at_2n.jsonl", OPUS, 2 * THRESHOLD)
        rc, out = run_hook(state_tmp, at_2n, "sess-cross")  # same session
        check(rc == 0, "2N band: exit 0")
        check(out.strip() != "", "2N band: advisory re-arms and fires again")

        # 4. Sonnet-only turn, well past threshold -> never fires.
        sonnet = write_transcript(fixtures, "sonnet.jsonl", SONNET, 3 * THRESHOLD)
        rc, out = run_hook(state_tmp, sonnet, "sess-sonnet")
        check(rc == 0, "sonnet-only: exit 0")
        check(out.strip() == "", "sonnet-only: no advisory (Opus-only guard)")

        # 5. Robustness: garbage stdin, empty stdin, missing transcript -> exit 0, silent.
        rc, out = run_hook(state_tmp, None, "sess-x", stdin_override="not json {{{")
        check(rc == 0 and out.strip() == "", "garbage stdin: exit 0, silent")
        rc, out = run_hook(state_tmp, None, "sess-x", stdin_override="")
        check(rc == 0 and out.strip() == "", "empty stdin: exit 0, silent")
        rc, out = run_hook(state_tmp, os.path.join(fixtures, "nope.jsonl"), "sess-x")
        check(rc == 0 and out.strip() == "", "missing transcript: exit 0, silent")

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll token-guard self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
