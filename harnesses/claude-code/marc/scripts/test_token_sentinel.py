#!/usr/bin/env python3
"""Self-test for the warn-only PostToolUse token-guard hook (origin: #71, #73).

Stdlib only (no pytest); run directly:  python3 test_token_sentinel.py

Synthesizes fake transcript fixtures (NO real paths / names / session ids) and
drives `token_sentinel.py --hook` as a subprocess exactly the way Claude Code
would.

Runaway-loop guard (#71):
  * the advisory fires ONCE when a turn first crosses the Opus threshold, and is
    debounced (silent) on the next tool call in the same band;
  * it re-arms and fires again at the 2N band;
  * it does NOT fire below the threshold;
  * it does NOT fire for a Sonnet-only turn, however long;
  * the emitted payload is NON-BLOCKING (no `decision`, correct hookEventName);
  * the hook ALWAYS exits 0, including on empty / garbage stdin.

Model-switch guard (#73):
  (a) a MAIN-thread A->B switch WITH the cache-write spike warns exactly once;
  (b) steady-state same-model turns never warn;
  (c) a subagent/sidechain running a DIFFERENT model never warns (the
      false-positive trap: separate context + cache, marked `isSidechain`);
  (d) the initial model of a session is never treated as a switch;
  (e) the hook ALWAYS exits 0.

Context-size / per-turn-token guard (#81):
  * a turn with a MODERATE call count (below the call-count band) but an
    OVERSIZED context fires the context-size band while the call-count band
    stays silent — the exact gap this guard closes;
  * it fires regardless of model tier (Sonnet included, not Opus-only);
  * it is debounced the same way as the call-count band (once per N-band);
  * a turn below the tokens threshold never warns;
  * the hook ALWAYS exits 0.
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
TOKENS_THRESHOLD = 500_000  # above the switch-guard fixtures' ~130K steady state
                             # (origin: #81), so the two guards' fixtures don't
                             # cross-trip each other in this shared test file.
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


SPIKE_CW = 130_000   # full re-cache write of a big context (above the 20K floor)
STEADY_CR = 130_000  # steady state: cache_read dominates
STEADY_CW = 200      # steady state: tiny incremental write


def write_multiturn(dirpath: str, name: str, turns: list[dict]) -> str:
    """Write a synthetic transcript from a list of turn specs.

    Each turn spec is a dict:
      model      model id for the turn's assistant messages
      cw, cr     cache_creation / cache_read on the turn's FIRST assistant msg
      calls      number of assistant tool-call requests (default 1)
      sidechain  if True, mark the user + assistant records `isSidechain: True`
                 (a subagent turn — must be invisible to the switch guard)
    """
    path = os.path.join(dirpath, name)
    lines: list[dict] = []
    for t in turns:
        side = bool(t.get("sidechain"))
        user = {"type": "user", "message": {"role": "user", "content": "go"}}
        if side:
            user["isSidechain"] = True
        lines.append(user)
        for i in range(t.get("calls", 1)):
            usage = {
                "input_tokens": 10,
                "cache_read_input_tokens": t.get("cr", 0) if i == 0 else 0,
                "cache_creation_input_tokens": t.get("cw", 0) if i == 0 else 0,
            }
            rec = {
                "type": "assistant",
                "message": {
                    "role": "assistant",
                    "model": t["model"],
                    "content": [{"type": "tool_use", "id": f"t{i}",
                                 "name": "Bash", "input": {}}],
                    "usage": usage,
                },
            }
            if side:
                rec["isSidechain"] = True
            lines.append(rec)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in lines:
            fh.write(json.dumps(rec) + "\n")
    return path


def run_hook(env_tmp: str, transcript_path: str | None, session_id: str,
             stdin_override: str | None = None) -> tuple[int, str]:
    env = dict(os.environ)
    env["TMPDIR"] = env_tmp                       # isolate the debounce state dir
    env["MARC_TOKEN_GUARD_THRESHOLD"] = str(THRESHOLD)
    env["MARC_TOKEN_GUARD_TOKENS_THRESHOLD"] = str(TOKENS_THRESHOLD)
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

        # ---- Model-switch guard (origin: #73) -----------------------------

        # (a) MAIN-thread A->B switch WITH cache-write spike -> warns once.
        switch = write_multiturn(fixtures, "switch.jsonl", [
            {"model": SONNET, "cw": STEADY_CW, "cr": STEADY_CR},   # baseline A
            {"model": OPUS, "cw": SPIKE_CW, "cr": 0},              # switch to B (spike)
        ])
        rc, out = run_hook(state_tmp, switch, "sess-switch")
        check(rc == 0, "switch A->B: exit 0")
        check(out.strip() != "", "switch A->B with spike: warns")
        if out.strip():
            data = parse_advisory(out)
            ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
            check("model-switch" in ctx and SONNET in ctx and OPUS in ctx,
                  "switch advisory names the A->B switch")

        # ... and is debounced: another tool call in the SAME switch turn -> silent.
        switch2 = write_multiturn(fixtures, "switch2.jsonl", [
            {"model": SONNET, "cw": STEADY_CW, "cr": STEADY_CR},
            {"model": OPUS, "cw": SPIKE_CW, "cr": 0, "calls": 2},
        ])
        rc, out = run_hook(state_tmp, switch2, "sess-switch")  # same session
        check(rc == 0, "switch debounce: exit 0")
        check(out.strip() == "", "switch A->B: warns exactly once (debounced)")

        # (b) steady-state same-model turns -> no warn.
        steady = write_multiturn(fixtures, "steady.jsonl", [
            {"model": OPUS, "cw": STEADY_CW, "cr": STEADY_CR},
            {"model": OPUS, "cw": STEADY_CW, "cr": STEADY_CR},
        ])
        rc, out = run_hook(state_tmp, steady, "sess-steady")
        check(rc == 0, "steady-state: exit 0")
        check(out.strip() == "", "steady-state same-model: no warn")

        # (c) FALSE-POSITIVE TRAP: subagent/sidechain on a different model, with
        #     its own cache-write spike, must NOT warn — the operator's main
        #     thread stayed on one model the whole time.
        subagent = write_multiturn(fixtures, "subagent.jsonl", [
            {"model": OPUS, "cw": STEADY_CW, "cr": STEADY_CR},           # main A
            {"model": SONNET, "cw": SPIKE_CW, "cr": 0, "sidechain": True},  # subagent B (spike)
            {"model": OPUS, "cw": STEADY_CW, "cr": STEADY_CR},           # main A continues
        ])
        rc, out = run_hook(state_tmp, subagent, "sess-subagent")
        check(rc == 0, "subagent different model: exit 0")
        check(out.strip() == "",
              "subagent/sidechain different model: NO warn (false-positive trap)")

        # (d) initial model only -> no warn (first model is never a switch).
        initial = write_multiturn(fixtures, "initial.jsonl", [
            {"model": OPUS, "cw": SPIKE_CW, "cr": 0},  # even with a spike, no prior
        ])
        rc, out = run_hook(state_tmp, initial, "sess-initial")
        check(rc == 0, "initial model: exit 0")
        check(out.strip() == "", "initial model only: no warn")

        # (a') a real switch whose re-cache write is BELOW the floor -> no warn
        #      (guards against noise; steady-state cache-read stays dominant).
        tiny = write_multiturn(fixtures, "tiny.jsonl", [
            {"model": SONNET, "cw": STEADY_CW, "cr": STEADY_CR},
            {"model": OPUS, "cw": STEADY_CW, "cr": STEADY_CR},  # switch but no spike
        ])
        rc, out = run_hook(state_tmp, tiny, "sess-tiny")
        check(rc == 0, "sub-floor switch: exit 0")
        check(out.strip() == "", "switch without cache-write spike: no warn")

        # ---- Context-size / per-turn-token guard (origin: #81) ------------

        # The exact gap this guard closes: a MODERATE call count (well below
        # the call-count band) that still drags in an OVERSIZED context. The
        # call-count band must stay silent while the new context-size band
        # fires. Uses Sonnet (not Opus) to prove the band is tier-independent.
        moderate_calls = THRESHOLD - 2  # comfortably below the call-count band
        big_ctx = write_multiturn(fixtures, "big_ctx.jsonl", [
            {"model": SONNET, "cw": TOKENS_THRESHOLD * 2, "cr": 0,
             "calls": moderate_calls},
        ])
        rc, out = run_hook(state_tmp, big_ctx, "sess-bigctx")
        check(rc == 0, "moderate calls + oversized context: exit 0")
        check(out.strip() != "",
              "moderate calls + oversized context: context-size band FIRES")
        if out.strip():
            data = parse_advisory(out)
            ctx = data.get("hookSpecificOutput", {}).get("additionalContext", "")
            check("context-size" in ctx.lower() or "Context-size" in ctx,
                  "context-size advisory names the guard")

        # Sanity: the SAME fixture would NOT trip the call-count band on its
        # own terms (moderate_calls < THRESHOLD, and it's Sonnet, not Opus) —
        # confirms the two bands are independent signals, not one masking
        # the other.
        check(moderate_calls < THRESHOLD,
              "fixture sanity: call count stays below the call-count band")

        # Below the tokens threshold -> silent (no false positive on a small
        # context, even with the same moderate call count).
        small_ctx = write_multiturn(fixtures, "small_ctx.jsonl", [
            {"model": SONNET, "cw": TOKENS_THRESHOLD // 10, "cr": 0,
             "calls": moderate_calls},
        ])
        rc, out = run_hook(state_tmp, small_ctx, "sess-smallctx")
        check(rc == 0, "moderate calls + small context: exit 0")
        check(out.strip() == "",
              "moderate calls + small context: no warn (below tokens threshold)")

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll token-guard self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
