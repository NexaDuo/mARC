#!/usr/bin/env python3
"""Token-throughput sentinel for Claude Code session logs.

Offline operator self-check: no network, zero token/dollar cost. Reads a Claude
Code session `.jsonl` and reports, per user turn, the model used, the number of
assistant tool calls, and the cost-weighted tokens processed (fresh input and
cache-write tokens at full rate, cache-read tokens discounted, origin: #100),
flagging turns that look runaway so the operator can tighten model tiering and
dispatch bounds.

Two modes share ONE counting implementation (DRY):

  * MANUAL CLI (default) — the operator diagnostic added in #69. Prints a
    per-turn table. Unchanged behaviour.
        python3 token_sentinel.py [SESSION.jsonl] [--calls N] [--tokens N]

  * PostToolUse HOOK (`--hook`, origin: #71, #73, #81) — warn-only, non-blocking
    automatic guards. Reads the hook's stdin JSON, inspects `transcript_path`,
    and may emit a non-blocking advisory (Claude Code
    `hookSpecificOutput.additionalContext` + a top-level `systemMessage`). It
    NEVER blocks, denies, or aborts a tool call, and ALWAYS exits 0. Three guards:
      - runaway-loop (#71): the CURRENT turn is on Opus AND has crossed a
        consecutive-tool-call threshold -> suggest `/compact` or a Sonnet drop.
      - model-switch (#73): a genuine MAIN-thread mid-session model switch
        (A->B) carrying the cache-invalidation fingerprint (cache-write spike,
        cache-read collapse) -> note the context was re-cached and suggest
        switching at a natural break / `/compact`. Subagent/sidechain model
        differences (`isSidechain`) are ignored — separate context and cache.
      - context-size (#81): the CURRENT turn's tokens-processed has crossed a
        threshold regardless of model tier or call count -> catches the case a
        MODERATE call count still drags in an oversized re-read context, below
        the runaway-loop band. Suggest `/compact` or a fresh session.
        <hook-json-on-stdin> | python3 token_sentinel.py --hook

Usage (CLI):
    python3 token_sentinel.py [SESSION.jsonl]
      SESSION.jsonl   explicit path to a session log; if omitted, the newest
                      `.jsonl` for the current working directory's project under
                      ~/.claude/projects/<encoded>/ is used (falling back to the
                      newest log across all projects).

    Thresholds (override with flags):
      --calls N       flag a turn with more than N assistant tool calls  (default 25)
      --tokens N      flag a turn processing more than N tokens           (default 150000)

    Env vars (shared with the hook, see below):
      MARC_TOKEN_GUARD_THRESHOLD          call-count band (default 25)
      MARC_TOKEN_GUARD_TOKENS_THRESHOLD   context-size / per-turn-token band (default 150000)

The per-turn token metric sums, over every assistant message in the turn,
`usage.input_tokens + usage.cache_read_input_tokens +
usage.cache_creation_input_tokens`. A "turn" is the span of assistant activity
following one real user message (tool-result feedback messages are not new turns).
"""
from __future__ import annotations

import argparse
import glob
import hashlib
import json
import os
import re
import sys
import tempfile

# --- Automatic-guard tuning (origin: #71) ----------------------------------
# Consecutive assistant tool-call API requests within one user turn that trip
# the Opus runaway advisory. Override with the env var; a turn that reaches N
# warns once, then again at 2N, 3N, ... (band debounce). Kept in lockstep with
# the manual CLI's default --calls so the two views agree on "runaway".
DEFAULT_HOOK_THRESHOLD = 25
HOOK_THRESHOLD_ENV = "MARC_TOKEN_GUARD_THRESHOLD"

# --- Context-size / per-turn-token guard (origin: #81, re-weighted #100) ----
# The call-count band above misses the case where a MODERATE number of tool
# calls (below the call-count threshold) still drags in an oversized context —
# a handful of large file reads or a big re-read can blow the token budget
# without ever crossing the call-count band. This band watches tokens
# processed in the CURRENT turn, independent of model tier: a large re-read
# costs real money on any tier, not just Opus. Override with the env var; a
# turn that reaches N warns once, then again at 2N, 3N, ... (same band
# debounce as the call-count guard, in its own state file so the two guards
# never clobber each other).
#
# The measure is WEIGHTED (origin: #100), not a flat sum. Anthropic bills a
# prompt-cache read (`cache_read_input_tokens`) at roughly 1/10th the rate of
# fresh input or a cache write (`input_tokens` + `cache_creation_input_tokens`,
# both full-rate). A flat `input + cache_read + cache_creation` sum treats a
# cheap re-read of an already-cached skill/context identically to an expensive
# fresh generation, so a normal large-skill session that reloads the same
# cached context turn after turn (all cache-read, little fresh) trips the band
# on tokens that cost a tenth of what they look like — a false positive, not a
# real blowup. CACHE_READ_WEIGHT below discounts cache-read tokens toward that
# real cost before banding.
CACHE_READ_WEIGHT = 0.1  # cache_read_input_tokens counted at ~1/10th rate; tune here only.

# Re-tuned against the WEIGHTED measure (previously 150_000 against the flat
# sum). Observed false positives pre-#100 were raw floors of ~213K-965K on
# turns dominated by cache-read (large-skill dispatch, 6-9 tool calls): at a
# 0.1x weight those turns land at roughly 20K-100K weighted tokens even in a
# fairly mixed (not purely cache-read) split, comfortably under this
# threshold. A genuinely generation/cache-creation-dominated turn of the same
# raw size weights close to its raw value and still crosses it. Override with
# the env var if a repo's real workload needs a different band.
DEFAULT_HOOK_TOKENS_THRESHOLD = 130_000
HOOK_TOKENS_THRESHOLD_ENV = "MARC_TOKEN_GUARD_TOKENS_THRESHOLD"

# --- Mid-session model-switch guard (origin: #73) --------------------------
# Switching the model mid-session invalidates the prompt cache: the prefix
# cached under model A cannot be reused by model B, so B's first call is a full
# cache-WRITE of the whole context (a spike in cache_creation_input_tokens) with
# cache_read_input_tokens collapsing to ~0 — the inverse of the steady state,
# where cache_read dominates. We only warn on a genuine MAIN-thread A->B switch
# that carries that cache-write fingerprint; a switch whose re-cache write is
# below this floor is too small to be worth a nudge. Override with the env var.
DEFAULT_SWITCH_MIN_CACHE_WRITE = 20_000
SWITCH_MIN_CACHE_WRITE_ENV = "MARC_MODEL_SWITCH_MIN_CACHE_WRITE"


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
    """Flat (unweighted) raw sum, kept for diagnostic display only. The band
    checks below use `weighted_usage_tokens`, not this."""
    return (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_read_input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )


def usage_components(usage: dict) -> tuple[int, int]:
    """Split one usage dict into (fresh, cache_read) token counts.

    `fresh` = input_tokens + cache_creation_input_tokens: both bill at full
    rate (a cache write is still a full-price write of the whole context).
    `cache_read` = cache_read_input_tokens: a discounted re-read of tokens
    already cached (origin: #100 — see CACHE_READ_WEIGHT above).
    """
    fresh = (
        int(usage.get("input_tokens", 0) or 0)
        + int(usage.get("cache_creation_input_tokens", 0) or 0)
    )
    cache_read = int(usage.get("cache_read_input_tokens", 0) or 0)
    return fresh, cache_read


def weighted_usage_tokens(fresh: int, cache_read: int) -> int:
    """Cost-weighted token measure: fresh tokens at full rate, cache-read
    tokens discounted by CACHE_READ_WEIGHT (origin: #100). This is the measure
    that feeds the context-size band, so a cache-read-dominated re-read of an
    already-cached context no longer counts the same as a fresh generation.
    """
    return fresh + round(cache_read * CACHE_READ_WEIGHT)


def dominant_token_type(fresh: int, cache_read: int) -> str:
    """Label for the advisory/report: which token type actually drove this
    turn's size, so an operator or a manual audit can tell a cheap cache-read
    re-read (false-positive shape) from a real generation/cache-creation spike
    (genuine-cost shape) at a glance (origin: #100).
    """
    return "cache-read-dominated" if cache_read > fresh else "generation-dominated"


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
    """Fold a session .jsonl into per-turn stats. Shared by the CLI and the hook.

    Each turn dict carries:
      prompt    first 60 chars of the user prompt (diagnostic only)
      model     last model seen in the turn ("-" if none)
      calls     total `tool_use` blocks across the turn's assistant messages
      requests  assistant API requests in the turn that made >=1 tool call
      tokens    weighted usage tokens across the turn's assistant messages
                (origin: #100 — fresh tokens full rate, cache-read tokens
                discounted by CACHE_READ_WEIGHT; this feeds the context-size
                band)
      raw_tokens     flat (unweighted) sum, same as pre-#100 `tokens` — kept
                     for diagnostic display
      fresh_tokens   summed input + cache_creation tokens (full-rate)
      cache_read_tokens  summed cache_read tokens (discounted)
      output_tokens  summed assistant output tokens (origin: #149 — not used
                     by any guard band above; additive field for the opt-in
                     token-telemetry recorder, which needs input/output/
                     cache-read/cache-write split separately, not just the
                     guard's folded weighted/fresh measures)
      input_tokens   summed raw `input_tokens` only (excludes cache_creation;
                     `fresh_tokens` above still folds the two together for the
                     guard band — this is a separate additive field, #149)
      cache_write_tokens  summed `cache_creation_input_tokens` only (same
                          split rationale as `input_tokens`, #149)
      last_ts        ISO timestamp of the last assistant message seen in the
                     turn ("" if none) — additive field, origin: #149

    Plus MAIN-THREAD-only fields for the model-switch guard (origin: #73), which
    deliberately EXCLUDE subagent/sidechain assistant messages (`isSidechain` is
    true): a specialist subagent runs on a different model in a SEPARATE context
    and cache, so its model choice is never a mid-session switch of the operator's
    thread. Counting it would fire a false warning on every dispatch.
      main_model  last model seen in a NON-sidechain assistant message ("-" none)
      cw          cache_creation_input_tokens of the FIRST main-thread assistant
                  message of the turn (the re-cache write, captured once)
      cr          cache_read_input_tokens of that same first main-thread message
    """
    turns: list[dict] = []
    current: dict | None = None

    def new_turn(prompt: str) -> dict:
        t = {"prompt": prompt, "model": "-", "calls": 0, "requests": 0,
             "tokens": 0, "raw_tokens": 0, "fresh_tokens": 0, "cache_read_tokens": 0,
             "output_tokens": 0, "input_tokens": 0, "cache_write_tokens": 0, "last_ts": "",
             "main_model": "-", "cw": 0, "cr": 0,
             "_main_seen": False}
        turns.append(t)
        return t

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
                current = new_turn(user_text(rec)[:60] or "(non-text prompt)")
                continue
            if rec.get("type") == "assistant":
                if current is None:
                    current = new_turn("(pre-first-user)")
                msg = rec.get("message") or {}
                model = msg.get("model")
                if model:
                    current["model"] = model
                n_calls = count_tool_calls(msg.get("content"))
                current["calls"] += n_calls
                if n_calls:
                    current["requests"] += 1
                usage = msg.get("usage")
                if isinstance(usage, dict):
                    current["raw_tokens"] += usage_tokens(usage)
                    fresh, cache_read = usage_components(usage)
                    current["fresh_tokens"] += fresh
                    current["cache_read_tokens"] += cache_read
                    current["tokens"] += weighted_usage_tokens(fresh, cache_read)
                    current["output_tokens"] += int(usage.get("output_tokens", 0) or 0)
                    current["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
                    current["cache_write_tokens"] += int(usage.get("cache_creation_input_tokens", 0) or 0)
                ts = rec.get("timestamp")
                if isinstance(ts, str) and ts:
                    current["last_ts"] = ts
                # Main-thread-only tracking for the switch guard (origin: #73):
                # ignore subagent/sidechain messages entirely.
                if rec.get("isSidechain") is True:
                    continue
                if model:
                    current["main_model"] = model
                if not current["_main_seen"] and isinstance(usage, dict):
                    current["_main_seen"] = True
                    current["cw"] = int(usage.get("cache_creation_input_tokens", 0) or 0)
                    current["cr"] = int(usage.get("cache_read_input_tokens", 0) or 0)
    return turns


# --- Automatic PostToolUse guard (origin: #71) ------------------------------

def is_opus_model(model: str) -> bool:
    """True for an Opus-tier model id; the guard only warns on Opus turns."""
    return "opus" in (model or "").lower()


def hook_threshold() -> int:
    raw = os.environ.get(HOOK_THRESHOLD_ENV, "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_HOOK_THRESHOLD
    return val if val > 0 else DEFAULT_HOOK_THRESHOLD


def hook_tokens_threshold() -> int:
    raw = os.environ.get(HOOK_TOKENS_THRESHOLD_ENV, "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_HOOK_TOKENS_THRESHOLD
    return val if val > 0 else DEFAULT_HOOK_TOKENS_THRESHOLD


def _state_path(session_key: str, kind: str = "") -> str:
    """Per-session debounce file under the system temp dir (no real paths leak).

    `kind` namespaces independent guards into separate files so the runaway
    guard (#71) and the model-switch guard (#73) never clobber each other's
    debounce state. `kind=""` preserves #71's original filename exactly.
    """
    digest = hashlib.sha256(session_key.encode("utf-8")).hexdigest()[:16]
    state_dir = os.path.join(tempfile.gettempdir(), "marc-token-guard")
    os.makedirs(state_dir, exist_ok=True)
    suffix = f"-{kind}" if kind else ""
    return os.path.join(state_dir, f"{digest}{suffix}.json")


def _load_state(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(path: str, state: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(state, fh)
    except OSError:
        pass  # warn-only: never fail a tool call over a state-file write.


def should_warn(*, model: str, count: int, threshold: int, turn_index: int,
                session_key: str) -> bool:
    """Debounced band check: warn once per threshold band (N, 2N, ...) per turn.

    Hooks are stateless per invocation, so the "already warned this band?"
    memory lives in a tiny per-session temp file keyed by session + turn. A new
    turn (fresh turn_index) starts a clean band, so the guard re-arms naturally.
    """
    if not is_opus_model(model):
        return False
    band = count // threshold  # 0 below N, 1 in [N,2N), 2 in [2N,3N), ...
    if band < 1:
        return False
    state_path = _state_path(session_key)
    state = _load_state(state_path)
    same_turn = state.get("turn") == turn_index
    warned_band = state.get("band", 0) if same_turn else 0
    if band <= warned_band:
        return False
    _save_state(state_path, {"turn": turn_index, "band": band})
    return True


def build_advisory(*, model: str, count: int, threshold: int) -> dict:
    """Non-blocking PostToolUse payload (Claude Code output contract).

    `hookSpecificOutput.additionalContext` reaches the model as a system
    reminder next to the tool result; the top-level `systemMessage` shows the
    operator a one-line heads-up. NEITHER blocks: no `decision`, no exit 2.
    """
    advice = (
        f"[mARC token-guard] Runaway-loop guard: this turn has made {count} "
        f"consecutive tool calls on an Opus-tier model ({model}), past the "
        f"{threshold}-call threshold. This is advisory only — nothing was "
        f"blocked. Consider `/compact` to shrink context, or dropping to a "
        f"Sonnet-tier model for the rest of this loop to bound token spend."
    )
    return {
        "systemMessage": (
            f"[mARC] token-guard: {count} consecutive Opus tool calls this turn "
            f"(>{threshold}). Advisory only. Consider /compact or Sonnet."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": advice,
        },
    }


# --- Context-size / per-turn-token guard (origin: #81) ----------------------

def should_warn_tokens(*, tokens: int, threshold: int, turn_index: int,
                       session_key: str) -> bool:
    """Debounced band check on tokens processed this turn, independent of
    model tier or call count — catches a moderate-call-count turn that still
    dragged in an oversized context. Same band-debounce shape as `should_warn`,
    a separate state file (`kind="tokens"`) so the two guards never clobber
    each other's memory.
    """
    band = tokens // threshold  # 0 below N, 1 in [N,2N), 2 in [2N,3N), ...
    if band < 1:
        return False
    state_path = _state_path(session_key, kind="tokens")
    state = _load_state(state_path)
    same_turn = state.get("turn") == turn_index
    warned_band = state.get("band", 0) if same_turn else 0
    if band <= warned_band:
        return False
    _save_state(state_path, {"turn": turn_index, "band": band})
    return True


def build_tokens_advisory(*, model: str, tokens: int, threshold: int,
                          fresh: int, cache_read: int) -> dict:
    """Non-blocking PostToolUse payload for the context-size / per-turn-token
    band (same channels as the other guards: no `decision`, no exit 2).

    `tokens` is the WEIGHTED measure (origin: #100), and the advisory names
    whether the turn is cache-read-dominated (likely a cheap re-read of
    already-cached context, so treat this as a heads-up rather than alarm) or
    generation-dominated (fresh tokens drove the size, so the cost is real).
    """
    approx_k = max(1, round(tokens / 1000))
    threshold_k = max(1, round(threshold / 1000))
    kind = dominant_token_type(fresh, cache_read)
    if kind == "cache-read-dominated":
        shape_note = (
            "Most of that is discounted prompt-cache re-reads (cheap), not "
            "fresh generation, so this is likely a large re-read rather than "
            "a real cost spike."
        )
    else:
        shape_note = (
            "Most of that is fresh input or a cache write, both full-rate, "
            "so this turn's cost is real."
        )
    advice = (
        f"[mARC token-guard] Context-size guard: this turn has processed "
        f"~{approx_k}K weighted tokens ({model}, {kind}), past the "
        f"{threshold_k}K-token threshold, even though the tool-call count may "
        f"still be low. {shape_note} This is advisory only, nothing was "
        f"blocked. Consider `/compact` to shrink context before continuing, "
        f"or starting a fresh session for the next task."
    )
    return {
        "systemMessage": (
            f"[mARC] token-guard: ~{approx_k}K weighted tokens processed this "
            f"turn (>{threshold_k}K, {kind}). Advisory only. Consider /compact."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": advice,
        },
    }


# --- Mid-session model-switch guard (origin: #73) ---------------------------

def switch_min_cache_write() -> int:
    raw = os.environ.get(SWITCH_MIN_CACHE_WRITE_ENV, "")
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SWITCH_MIN_CACHE_WRITE
    return val if val > 0 else DEFAULT_SWITCH_MIN_CACHE_WRITE


def is_cache_invalidation(cw: int, cr: int, floor: int) -> bool:
    """Cache-invalidation fingerprint: a cache-WRITE spike with cache-READ
    collapsed. Steady state is the inverse (cache_read dominates, tiny write).
    The absolute floor keeps small turns from tripping the guard.
    """
    return cw >= floor and cw > cr


def detect_switch(turns: list[dict], floor: int):
    """Return (from_model, to_model, turn_index, cw) for a genuine MAIN-thread
    mid-session model switch at the latest main-thread turn, else None.

    Only turns with a main-thread model (`main_model != "-"`) are considered, so
    subagent/sidechain activity is invisible here. The FIRST such turn is the
    baseline (never a switch). A switch is the latest main-thread turn whose
    model differs from the previous main-thread turn AND whose first main-thread
    call carries the cache-invalidation fingerprint.
    """
    main_turns = [
        (i, t) for i, t in enumerate(turns)
        if t.get("main_model", "-") != "-"
    ]
    if len(main_turns) < 2:
        return None  # first model in a session is not a switch
    (cur_i, cur), (_, prev) = main_turns[-1], main_turns[-2]
    if cur["main_model"] == prev["main_model"]:
        return None
    if not is_cache_invalidation(cur.get("cw", 0), cur.get("cr", 0), floor):
        return None
    return prev["main_model"], cur["main_model"], cur_i, cur.get("cw", 0)


def should_warn_switch(*, session_key: str, turn_index: int,
                       from_model: str, to_model: str) -> bool:
    """Debounce: warn ONCE per genuine switch event. A switch event is keyed by
    its turn index plus the (from -> to) pair, so a later flip (B->A, or a new
    A->B at a new turn) re-arms and warns again, but repeated tool calls within
    the same switch turn stay silent.
    """
    path = _state_path(session_key, kind="switch")
    state = _load_state(path)
    if (state.get("turn") == turn_index
            and state.get("from") == from_model
            and state.get("to") == to_model):
        return False
    _save_state(path, {"turn": turn_index, "from": from_model, "to": to_model})
    return True


def build_switch_advisory(*, from_model: str, to_model: str, cw: int) -> dict:
    """Non-blocking PostToolUse payload for a mid-session model switch. Same
    channels as the runaway guard (#71): no `decision`, no exit 2.
    """
    approx_k = max(1, round(cw / 1000))
    advice = (
        f"[mARC model-switch guard] Mid-session model switch detected "
        f"({from_model} -> {to_model}). Switching models invalidates the prompt "
        f"cache: the ~{approx_k}K-token context was just re-cached under the new "
        f"model as a full cache-write instead of a cheap cache-read, and every "
        f"flip repeats that cost. This is advisory only — nothing was blocked. "
        f"Prefer escalating at a natural context break (or `/compact` first), "
        f"and avoid flip-flopping models turn-by-turn."
    )
    return {
        "systemMessage": (
            f"[mARC] model-switch guard: mid-session switch {from_model} -> "
            f"{to_model} re-cached ~{approx_k}K tokens (full cache-write). "
            f"Advisory only. Prefer a natural break or /compact before switching."
        ),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": advice,
        },
    }


def _merge_advisories(advisories: list[dict]) -> dict | None:
    """Combine one or more non-blocking payloads into a single PostToolUse
    payload (a hook may only emit one JSON object). A single advisory passes
    through unchanged, so the #71 output contract is byte-for-byte preserved.
    """
    advisories = [a for a in advisories if a]
    if not advisories:
        return None
    if len(advisories) == 1:
        return advisories[0]
    return {
        "systemMessage": "\n".join(a["systemMessage"] for a in advisories),
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n\n".join(
                a["hookSpecificOutput"]["additionalContext"] for a in advisories
            ),
        },
    }


def run_hook(stdin_text: str) -> int:
    """Warn-only PostToolUse entrypoint. ALWAYS returns 0; never raises out."""
    try:
        payload = json.loads(stdin_text) if stdin_text.strip() else {}
    except json.JSONDecodeError:
        return 0
    if not isinstance(payload, dict):
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

    turn_index = len(turns) - 1  # current (last) turn
    current = turns[turn_index]
    model = current.get("model", "-")
    count = current.get("requests", 0)
    tokens = current.get("tokens", 0)  # weighted measure, origin: #100
    fresh_tokens = current.get("fresh_tokens", 0)
    cache_read_tokens = current.get("cache_read_tokens", 0)
    threshold = hook_threshold()
    tokens_threshold = hook_tokens_threshold()
    session_key = str(payload.get("session_id") or transcript_path)

    advisories: list[dict] = []

    # Guard 1 (#71): runaway Opus tool-loop.
    if should_warn(model=model, count=count, threshold=threshold,
                   turn_index=turn_index, session_key=session_key):
        advisories.append(build_advisory(model=model, count=count,
                                          threshold=threshold))

    # Guard 2 (#73): genuine main-thread mid-session model switch.
    switch = detect_switch(turns, switch_min_cache_write())
    if switch is not None:
        from_model, to_model, switch_turn, cw = switch
        if should_warn_switch(session_key=session_key, turn_index=switch_turn,
                              from_model=from_model, to_model=to_model):
            advisories.append(build_switch_advisory(
                from_model=from_model, to_model=to_model, cw=cw))

    # Guard 3 (#81): context-size / per-turn-token band, independent of the
    # call-count band above — catches a moderate-call-count turn with an
    # oversized re-read context.
    if should_warn_tokens(tokens=tokens, threshold=tokens_threshold,
                          turn_index=turn_index, session_key=session_key):
        advisories.append(build_tokens_advisory(
            model=model, tokens=tokens, threshold=tokens_threshold,
            fresh=fresh_tokens, cache_read=cache_read_tokens))

    merged = _merge_advisories(advisories)
    if merged is not None:
        sys.stdout.write(json.dumps(merged))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Per-turn token/tool-call sentinel for Claude Code logs.")
    ap.add_argument("session", nargs="?", help="path to a session .jsonl (default: newest for this project)")
    ap.add_argument("--calls", type=int, default=DEFAULT_HOOK_THRESHOLD,
                    help="flag turns above this tool-call count")
    ap.add_argument("--tokens", type=int, default=DEFAULT_HOOK_TOKENS_THRESHOLD,
                    help="flag turns above this WEIGHTED token count "
                         "(context-size signal, origin: #81; weighting origin: #100)")
    ap.add_argument("--hook", action="store_true",
                    help="run as a warn-only PostToolUse hook (reads hook JSON on stdin; always exits 0)")
    args = ap.parse_args(argv)

    if args.hook:
        # Warn-only guard: swallow every unexpected failure and still exit 0.
        try:
            return run_hook(sys.stdin.read())
        except Exception:  # noqa: BLE001 — a hook must never break a tool call.
            return 0

    path = args.session or newest_session_log(os.getcwd())
    if not path:
        print("no session .jsonl found (pass an explicit path)", file=sys.stderr)
        return 2
    if not os.path.isfile(path):
        print(f"not a file: {path}", file=sys.stderr)
        return 2

    turns = analyze(path)
    print(f"session: {path}")
    print(f"turns:   {len(turns)}   thresholds: >{args.calls} calls, >{args.tokens} weighted "
          f"tokens (context-size signal, origin: #81; cache-read weighted "
          f"{CACHE_READ_WEIGHT}x, origin: #100)\n")
    print(f"{'#':>3}  {'model':<28} {'calls':>6} {'tokens':>12}  {'dominant':<20} {'flag':<14} prompt")
    flagged = 0
    total_tokens = 0
    total_raw_tokens = 0
    for i, t in enumerate(turns, 1):
        total_tokens += t["tokens"]
        total_raw_tokens += t.get("raw_tokens", t["tokens"])
        over_calls = t["calls"] > args.calls
        over_tokens = t["tokens"] > args.tokens
        # Distinct flags so a manual audit can see WHICH signal tripped: a
        # moderate call count with an oversized context (CONTEXT-SIZE) is a
        # different failure mode than a long tool-call loop (RUNAWAY-CALLS).
        if over_calls and over_tokens:
            flag = "RUNAWAY+CTX"
        elif over_calls:
            flag = "RUNAWAY"
        elif over_tokens:
            flag = "CONTEXT-SIZE"
        else:
            flag = ""
        if over_calls or over_tokens:
            flagged += 1
        dominant = dominant_token_type(t.get("fresh_tokens", 0), t.get("cache_read_tokens", 0))
        print(f"{i:>3}  {t['model']:<28} {t['calls']:>6} {t['tokens']:>12}  {dominant:<20} {flag:<14} {t['prompt']}")
    print(f"\nweighted tokens processed: {total_tokens}   raw tokens: {total_raw_tokens}   "
          f"flagged turns: {flagged}")
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
