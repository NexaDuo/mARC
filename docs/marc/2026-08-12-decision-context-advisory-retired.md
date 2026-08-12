# Decision record: the context-size advisory is retired, not fixed

- **Type:** decision record
- **Date:** 2026-08-12
- **Attribution:** operator + user decision on
  [issue #181](https://github.com/NexaDuo/mARC/issues/181), superseding the
  fail-closed phase-1 fix shipped in v0.22.2 and cancelling the phase-2
  status-line bridge that #181 had left open.
- **Status:** accepted (code change; no version bump — released separately)

## Context

`token_sentinel.py`'s PostToolUse hook ran three advisory guards: a
runaway-tool-call-loop guard (#71), a mid-session model-switch guard (#73),
and a context-size guard (#81) that watched weighted tokens-processed per
turn and suggested `/compact` when a turn's context grew large. The
context-size guard needed a context-window value to band against, but Claude
Code does not publish the real window to hooks (confirmed by a `@research`
brief on #181: at least three to four independent upstream feature requests
— `anthropics/claude-code#11008`, `#34340`, `#44790` — ask for this and none
has shipped). The guard's first version (#178/PR #179) derived its band from
`MARC_CONTEXT_WINDOW`, falling back to an assumed 200K window when the var
was unset. That fallback caused the original false positive: on a 1M-window
session with the var unset, the guard fired against a 130K band derived from
a window that wasn't the session's. Phase 1 (#181/PR #182, shipped in
v0.22.2) made the guard fail-closed instead of guessing — no trustworthy
window meant no advisory, not an assumed one.

## The finding that changed it

Before starting the planned phase 2 (a status-line bridge to detect the real
window), the user asked the question that should have preceded #178
entirely: does the harness already handle this itself? Checked directly
against `code.claude.com` docs rather than recollection:

| Fact | Source |
|---|---|
| `autoCompactEnabled` — default `true`: "Automatically compact the conversation when context approaches the limit." | settings |
| `autoCompactWindow` — "How full the context window gets before Claude Code compacts automatically, in tokens from `100000` to `1000000`. When unset, Claude Code uses a window tuned for your model." | settings |
| Configurable via `/autocompact`, the `--autocompact` flag, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW` | settings |
| A context or auto-compact warning is not a usage limit — it fires when the conversation has grown close to the session's auto-compact window, and the harness emits it itself | costs |
| The status line can display context-window usage natively | statusline |

The harness does not merely have a sense of context volume. It knows the
model's real window, warns on it, and auto-compacts by default. mARC's
context-size advisory was a strictly worse implementation of a feature the
harness already ships: it declared the window instead of knowing it, and
could only suggest `/compact` — never act.

## Decision

1. **The status-line bridge (phase 2) is cancelled, not deferred.** Its only
   purpose was to restore a signal the harness already provides, better.
2. **The context-size advisory is removed from `token_sentinel.py`
   entirely** — `context_window()`, `MARC_CONTEXT_WINDOW`,
   `DEFAULT_CONTEXT_WINDOW`, `CONTEXT_WINDOW_FRACTION`,
   `MIN_CONTEXT_FRACTION_TO_WARN`, `hook_tokens_threshold()`,
   `MARC_TOKEN_GUARD_TOKENS_THRESHOLD`, and the `max_context` per-turn
   snapshot added for it in #178 — along with the guard-3 branch in
   `run_hook()` and its debounce-state file kind. The manual CLI's
   `--tokens` flag survives, with a new self-contained default
   (`DEFAULT_CLI_TOKENS_THRESHOLD`) that does not derive from a window: it is
   an explicit, operator-invoked report column, not a silent hook assumption.
3. **The two guards that are not about context occupancy are kept
   unchanged**, because the harness does not cover them and they are where
   the sentinel earns its place:
   - **#71 — runaway tool-call loops.** A long consecutive-tool-call streak
     on an Opus-tier model is a cost/pacing signal independent of how much of
     the context window is actually full.
   - **#73 — mid-session model switch.** Switching models invalidates the
     prompt cache regardless of context occupancy — a cost/cache-locality
     signal, not a window signal. The harness's auto-compact has no
     awareness of this at all.

## Why cancel rather than finish

Building the bridge would have meant spending real work to draw level with a
native feature, then carrying a status-line wrapper forever — colliding with
the user's single `statusLine` slot (the very zero-config cost that made
#181 uncomfortable in the first place). Duplicating a mechanism that
measures correctly with one that guesses is exactly how #178's false
positive happened; building a better guess instead of asking whether a guess
was ever needed would have repeated the same mistake one layer down.

## Cost of this decision, stated plainly

This writes off v0.22.1 in full and most of v0.22.2. #178/PR #179
(window-aware banding, headroom gating, the sidechain fix to `max_context`)
and #181/PR #182 (fail-closed) were both careful, reviewed work on a feature
that should not have existed. The error was not in the implementations; it
was in never checking whether the harness already owned the problem before
dispatching #178. That check cost one documentation fetch.

## Not a regression for the other harnesses

Retiring the advisory removes it from Antigravity and Copilot too. In
practice this changes nothing there: since v0.22.2 the guard was already
fail-closed and silent on any harness without `MARC_CONTEXT_WINDOW` set.
Whether Antigravity and Copilot have their own native context/compaction
handling comparable to Claude Code's is tracked separately, not by this
record.

## Consequences

- `token_sentinel.py` ships two guards instead of three; its test suite
  drops the removed guard's cases while keeping explicit regression coverage
  that #71 and #73 still fire correctly.
- The manual CLI's `--tokens`/`--calls` report keeps working unchanged for
  operator diagnostics.
- No plugin version bump in this change; the release (version bump +
  `CHANGELOG.md` entry landing in a tagged release) is a separate step.

## Revisit conditions

- Anthropic ships a documented, hook-accessible context/token-usage field
  (closing any of `anthropics/claude-code#11008`/`#34340`/`#44790`) — at that
  point a context-size signal could be rebuilt on real data instead of a
  guess, without owning a status-line bridge.
- A future guard is proposed that measures something the harness's
  auto-compact does not cover (as #71 and #73 already do) — check against
  this record's reasoning before building it, rather than re-deriving the
  window-guessing approach from scratch.

## Sources

- [issue #181](https://github.com/NexaDuo/mARC/issues/181) and its
  `@research` brief, decision, and phase-1 comments
- [Claude Code Docs: Settings — `autoCompactEnabled` / `autoCompactWindow`](https://code.claude.com/docs/en/settings)
- [Claude Code Docs: Costs — context/auto-compact warning](https://code.claude.com/docs/en/costs)
- [Claude Code Docs: Status line](https://code.claude.com/docs/en/statusline)
- `anthropics/claude-code` issues `#11008`, `#34340`, `#44790` (upstream
  requests to expose context/token usage to hooks; none shipped)
