# Research Brief: Google Antigravity (`agy`) Context Compaction & Harness Parity

**Produced by:** `@research`  
**Target Issue:** [#186](https://github.com/NexaDuo/mARC/issues/186) — *research(antigravity): does agy have native context compaction? — the one harness #181's retirement can't lean on*  
**Date:** 2026-08-24  
**Status:** Complete / Evidence-Backed  

---

## 1. Executive Summary

When mARC retired its token sentinel context-size advisory in [#181](https://github.com/NexaDuo/mARC/issues/181), it did so on the principle that the host harness natively measures and manages context compaction (e.g., Claude Code’s automatic compaction and Copilot CLI’s fixed background compaction). Issue #186 was commissioned to resolve whether Google Antigravity CLI (`agy`) possesses an equivalent native context compaction mechanism or represents an unmitigated gap.

**Key Findings:**
1. **Client-Side Compaction is Absent / Undocumented in `agy`:** Official `antigravity.google/docs/cli/*` documentation contains zero references to a `/compact` command, auto-compaction settings, or compaction thresholds in `settings.json`. Developer discussions on `discuss.ai.google.dev` confirm that `agy` lacks Claude-Code-like native `/compact` or background summarization; users regularly hit hard "prompt is too long" / 200k overflow terminations during long sessions.
2. **Built-in Session Controls Exist Instead:** Rather than automated context compaction, Antigravity provides explicit session-branching and inspection commands: `/context` (visualizer panel), `/rewind` / `/undo` (reverting turn state), `/fork` / `/branch` (cloning workspace into an isolated session), and `/clear` / `/new` (context reset).
3. **Status Line Exposes Full Context Metrics:** `agy` natively supports a rich `statusLine.command` interface that receives a detailed `context_window` JSON payload on `stdin` (`context_window_size`, `used_percentage`, `remaining_percentage`, `total_input_tokens`, `total_output_tokens`, `current_usage`, and `exceeds_200k_tokens`).
4. **Architectural Recommendation for mARC:** **No bridge, no guard revival; document in `COMPATIBILITY.md`.** mARC should not resurrect the context-size advisory or construct a status-line bridge for Antigravity. Constructing a status-line bridge would monopolize the user's sole `statusLine` configuration slot in `settings.json` and violate mARC's zero-config invariant. Furthermore, because `agy` lacks a `/compact` command, any advisory would only be able to recommend starting a fresh session or forking—which mARC’s compiled prompts already do (`compact_action: "start a fresh session"`).

---

## 2. Primary Sources & Findings

### Findings Breakdown by Question

#### Q1 & Q2: Native Context Compaction Behavior & Documentation Status
* **Official Documentation (`antigravity.google/docs/cli/*`):** `[measured/doc — High Confidence]`
  * Checked all 29 documented slash commands across `reference`, `using`, `features`, `settings`, `plugins`, and `statusline`.
  * **No `/compact` slash command exists.** 
  * Checked all documented `settings.json` keys (`toolPermission`, `artifactReviewPolicy`, `enableTerminalSandbox`, `verbosity`, `useG1Credits`, `statusLine`, etc.). **No auto-compaction toggle, threshold, or window configuration key exists.**
  * No environment variables (e.g. `AGY_AUTO_COMPACT`) or CLI launch flags for context compaction are documented.
* **Developer Community & Issue Reports (`discuss.ai.google.dev`, Reddit):** `[reported — Medium/High Confidence]`
  * Multiple developer threads on the Google AI Developers Forum explicitly report the lack of native, graceful context compaction in Antigravity as a major UX gap compared to Claude Code and Cursor.
  * Developers report hitting hard errors ("prompt is too long" / context window exhaustion at 200k tokens) during long-running agent workflows.
  * Several open feature requests push for `/compact`, `/snapshot`, or message pruning.
  * While opaque server-side history truncation/compression during plan execution has been observed in backend pipelines, it is unpredictable, non-configurable, and not a client-managed auto-compaction mechanism.
* **Documented Native Context Tools:** `[measured/doc — High Confidence]`
  * `/context`: Opens an interactive context usage visualization panel.
  * `/rewind` / `/undo`: Rolls back turns to discard unneeded context.
  * `/fork` (or `/branch`): Forks the current conversation and workspace into an independent session, preserving initial state while isolating subsequent context growth.
  * `/clear` (or `/new`): Completely clears session context.

#### Q3: Status Line Context Window Schema
* **Status Line Payload Contract:** `[measured/doc — High Confidence]`
  * Defined under `antigravity.google/docs/cli/statusline` and configured via `~/.gemini/antigravity-cli/settings.json` (`"statusLine": {"type": "command", "command": "..."}`).
  * On every agent state change, `agy` pipes a JSON object to the command's `stdin`.
  * The `context_window` payload contains:
    * `context_window_size` (integer): Maximum tokens for the active model window.
    * `used_percentage` (float): Percentage of window currently consumed.
    * `remaining_percentage` (float): Percentage remaining.
    * `total_input_tokens` & `total_output_tokens` (integers): Lifetime token counters.
    * `current_usage` (object): Granular category breakdown.
    * `exceeds_200k_tokens` (boolean): Threshold alert flag.

---

## 3. Harness Comparison Matrix

| Capability / Feature | Claude Code | GitHub Copilot CLI | Google Antigravity (`agy` CLI) |
| :--- | :--- | :--- | :--- |
| **Native Auto-Compaction** | **Yes** (on by default via `autoCompactEnabled: true`) `[measured]` | **Yes** (on by default; background compaction begins at ~80% window, pauses at ~95%) `[measured]` | **Absent / Undocumented** (no client-side auto-compaction or `/compact` command) `[measured/negative]` |
| **Compaction Threshold Config** | **Tunable** via `autoCompactWindow` (100k–1M), `/autocompact`, `--autocompact`, or `CLAUDE_CODE_AUTO_COMPACT_WINDOW` `[measured]` | **Fixed** at ~80%/95% (unresolved upstream FR #1761) `[measured]` | **None** (no settings key or flag) `[measured/negative]` |
| **Native Warning Signal** | **Yes** (emits distinct context/auto-compact warning before window exhaustion) `[measured]` | **Implicit** (pauses execution at 95% while compaction completes) `[measured]` | **None** (fails with prompt overflow or unannounced backend cutoff) `[reported]` |
| **Manual Compaction Command** | `/compact` `[measured]` | None (handled automatically) `[measured]` | None (uses `/context`, `/fork`, `/rewind`, `/clear`) `[measured]` |
| **Context Window in Hooks** | **No** (no token/context fields on hook inputs) `[measured]` | **No** (no token/context fields on hook inputs) `[measured]` | **No** (no token/context fields in native `PreInvocation`/`PostToolUse`/`Stop` hooks) `[measured]` |
| **Status Line Context Telemetry** | **Yes** (`context_window` object on `statusLine`) `[measured]` | **Experimental** (`statusLine.command` with token fields) `[reported]` | **Yes** (`context_window` schema with `used_percentage`, `exceeds_200k_tokens`, etc.) `[measured]` |
| **mARC `compact_action` Target** | `{{ compact_action }}` compiles to `/compact` `[measured]` | `{{ compact_action }}` compiles to `start a fresh session` `[measured]` | `{{ compact_action }}` compiles to `start a fresh session` `[measured]` |

---

## 4. Recommendations for mARC

### 1. Do Not Build a Status-Line Bridge & Do Not Revive Context-Size Guard
* **Status-Line Slot Conflict:** Antigravity provides a single `statusLine` configuration in `settings.json`. If mARC claimed this slot for a custom context monitor bridge, it would clobber user status-line scripts, violating mARC's zero-config and non-intrusive plugin contract.
* **No Actionable Remediation:** In Claude Code, an advisory tells the user to run `/compact`. In Antigravity, `/compact` does not exist. Suggesting `/fork` or session reset on an estimated token count was already retired in [#181](https://github.com/NexaDuo/mARC/issues/181) for causing false alarms on large-context sessions.
* **Active Guards Already Protect Antigravity:** The surviving `token_sentinel.py` guards (#71 runaway tool loops and #73 mid-session model switches) operate identically across Claude Code, Antigravity, and Copilot CLI, catching token waste without guessing context window limits.

### 2. Update `harnesses/antigravity/marc/COMPATIBILITY.md`
Add an explicit entry under the Feature Matrix and Architectural Mappings in `harnesses/antigravity/marc/COMPATIBILITY.md`:
* **Row Addition:**
  * **Feature:** `Context Management & Compaction`
  * **Claude Code Behavior:** Native `/compact` and configurable background auto-compaction (`autoCompactEnabled`).
  * **Google Antigravity Behavior:** No native `/compact` or auto-compaction settings. Provides `/context`, `/fork`, `/rewind`, and `/clear`. Real-time context usage is exposed via `statusLine` JSON payload (`context_window`).
  * **Compatibility Status:** `Harness Disparity (Handled by Prompt Compilation)`
  * **Action Needed / Notes:** Document that `token_sentinel.py` context-size guard is retired (#181); mARC compiles techlead prompt with `compact_action: "start a fresh session"` under Antigravity.

### 3. Archive Durable Research Artifact
Materialize this brief in `docs/marc/` (`docs/marc/2026-08-24-brief-agy-context-compaction.md`) following the durable team artifact policy ([#46](https://github.com/NexaDuo/mARC/issues/46)).

---

### Coverage & Citations
* **Fetched & Analyzed:**
  * `antigravity.google/docs/cli/reference` (slash command registry)
  * `antigravity.google/docs/cli/settings` (configuration keys & precedence)
  * `antigravity.google/docs/cli/statusline` (`context_window` payload schema)
  * `antigravity.google/docs/cli/context` & `antigravity.google/docs/cli/using` (session scoping, fork, rewind)
  * `discuss.ai.google.dev` forum threads regarding Antigravity context limits & compaction requests
  * `code.claude.com/docs/en/settings`, `code.claude.com/docs/en/costs`, `code.claude.com/docs/en/statusline`
  * `docs.github.com/en/copilot/concepts/agents/copilot-cli/context-management`
  * Internal repo files: `harnesses/antigravity/marc/compile.json`, `harnesses/antigravity/marc/COMPATIBILITY.md`, `core/scripts/token_sentinel.py`, `docs/marc/2026-08-12-brief-harness-context-management.md`, `docs/marc/2026-08-12-decision-context-advisory-retired.md`.
