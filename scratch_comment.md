## Research brief: Token optimization techniques from Spotify's Claude Code architecture (Issue #251)

**TL;DR**
Dimitri Mazmanov's September 3, 2026 Spotify Engineering blog post demonstrates a reported ~90% reduction in Claude Code token usage by shunting mechanical I/O tasks away from expensive frontier models to cheap worker runtimes (Gemini 2.5 Flash). The approach couples deterministic `PreToolUse` interceptor hooks (`check-file-size`, `check-bash-read`) with specialized worker modes (`bulk-reader`, `code-writer`) and agent skills. For mARC, adapting this strategy is viable and directly aligns with our cross-harness architecture (`dispatch_agent.py`), provided we avoid Spotify's current hook schema failure modes (issue #10 in `spotify/portal-ai-plugins`) and preserve unmutilated inputs for security and correctness reviewers (`@sec` and `@rev`).

### Findings

- **Bulk I/O offloading yields ~90% token reduction in large repos** [measured]
  Spotify Engineering Blog, September 3, 2026, `https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90`.
  > "In tests against a 162,000-line Java monorepo, offloading bulk file reads and boilerplate scaffolding to lightweight worker models cut token consumption by an average of 90% (ranging from 82% to 94%)."
  The post demonstrates that coding agents expend most of their tokens on I/O operations (ingesting whole files for context and emitting boilerplate) rather than actual reasoning. Feeding entire files into frontier models quickly exhausts the context window and inflates cache write/read costs.

- **Three-tier separation: Hooks (Enforcement), Modes/Scripts (Execution), Skills (Guidance)** [reported]
  GitHub repository `spotify/portal-ai-plugins`, September 2026, `https://github.com/spotify/portal-ai-plugins`.
  > "The architecture splits routing into three distinct layers: PreToolUse hooks enforce boundaries deterministically, bash/CLI scripts handle transport and worker invocation, and skills guide the model on when to delegate."
  The `shunt` plugin acts as an automated traffic director. `bulk-reader` ingests large files and returns concise bullet points to the frontier model, while `code-writer` takes a specification and a reference file to generate code directly to disk (`--target`), bypassing the primary model's context window.

- **Deterministic PreToolUse interception with targeted bypasses** [reported]
  Spotify Engineering Blog, September 3, 2026, `https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90`.
  > "The check-file-size hook intercepts Read calls exceeding a configurable threshold (default 350 lines), while check-bash-read blocks unpiped cat, head, tail, less, and more commands. Targeted reads specifying offset/limit or piped commands (e.g. cat file | grep) pass through untouched."
  The file threshold is configured via `SHUNT_MIN_LINES` (default: 350). Targeted operations are intentionally permitted because the agent has already narrowed its query, preventing unnecessary delegation latency.

- **Delegation introduces a latency tax of 10 to 30 seconds** [measured]
  Spotify Engineering Blog, September 3, 2026, `https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90`.
  > "Each round-trip delegation to a remote worker mode via the Portal CLI introduces approximately 10–30 seconds of network and execution latency."
  While token costs drop drastically, wall-clock time increases for turns requiring worker delegation. Consequently, small files (< 350 lines) or interactive debugging passes should remain local.

- **Worker models fail at complex reasoning and lack security awareness** [reported]
  Spotify Engineering Blog, September 3, 2026, `https://engineering.atspotify.com/2026/9/portal-by-spotify-cut-my-claude-code-token-usage-by-90`.
  > "Worker models (like Gemini 2.5 Flash) are suited strictly for mechanical extraction, summarization, and pattern-matched boilerplate. They cannot be trusted for nuanced debugging, architectural trade-offs, or safety-critical logic."

- **Known failure mode in Spotify's open-source release: Hook schema mismatch (#10)** [measured]
  GitHub Issue #10 (`spotify/portal-ai-plugins/issues/10`), "shunt hooks use the wrong PreToolUse output schema and are not blocking", September 2026.
  > "The shunt hooks emit an obsolete top-level decision field rather than hookSpecificOutput.permissionDecision, causing Claude Code to fail-open. Tool calls proceed unblocked while an error notice is printed in transcripts."
  Additionally, `code-writer` in Spotify's repository lacks active hook enforcement (it is guided purely by prompt instructions) and risks overwriting files unless explicit safety flags are set.

### Implications for the Decision (Actionable Recommendations for mARC)

1. **Add `PreToolUse` Hook Support to Claude Code Compilation in mARC**
   - *Current state*: In `scripts/compile_prompts.py`, `_CC_EVENT_MAP` only maps `session_start`, `session_start_compact`, `post_tool_use`, and `stop`. `PreToolUse` is mapped for `antigravity` but omitted for `claude-code`.
   - *Recommendation*: Update `_CC_EVENT_MAP` to support `pre_tool_use: "PreToolUse"`. This enables mARC to define deterministic pre-execution guardrails across both harnesses.

2. **Implement an Opt-In PreToolUse Read Guard (`read-guard.sh`) Avoiding Spotify's Schema Bug**
   - *Current state*: mARC's `token-guard.sh` is purely a `PostToolUse` warn-only hook (origin: #71, #73). It alerts after runaway loops or model switches, but cannot prevent the initial token dump from a 2,000-line file read.
   - *Recommendation*: Implement an opt-in `read-guard.sh` script. Learn from Spotify's bug (#10): use Claude Code's valid contract, exiting with code `2` or returning `{"hookSpecificOutput": {"permissionDecision": "deny", "permissionDecisionReason": "File exceeds threshold..."}}`.
   - *Permit targeted reads*: Ensure requests with `offset` / `limit` or grep operations pass without blocking.
   - *Exempt security and correctness roles*: Per mARC's existing rule in `core/agents/research.md` and `security.md` (origin: #137, #227), `` `@sec` `` and `` `@rev` `` must never reason over truncated or summarized inputs. The read guard must bypass or allow unconstrained reads when invoked by audit specialists.

3. **Leverage mARC's Poly-Model Routing (`dispatch_agent.py`) as the Worker Engine**
   - *Current state*: Spotify relies on an internal, proprietary platform ("Portal by Spotify") and serverless "AiKA" modes to invoke Gemini 2.5 Flash.
   - *Recommendation*: mARC already contains a harness-neutral delegation router in `core/scripts/dispatch_agent.py` (origin: #239, #241) and `DEFAULT_HYBRID_MATRIX`, which can route tasks across `claude-code`, `antigravity`, and `copilot`. We can utilize `dispatch_agent.py` to route heavy extraction or research tasks directly to cheaper harnesses/models (e.g. Gemini via Antigravity) without creating dependencies on closed infrastructure.

4. **Adopt "Disk-First" Boilerplate Scaffolding for `` `@dev` ``**
   - *Current state*: `` `@dev` `` often streams boilerplate test setups, DTOs, and interface definitions through the main chat context.
   - *Recommendation*: Add clear instructions in `core/agents/engineer.md` directing `` `@dev` `` to scaffold repetitive files directly to disk via background scripts or isolated worktrees, avoiding streaming large blocks of predictable syntax into the conversation history.

5. **Expose Line-Threshold Configuration in `.agents/team.toml`**
   - *Current state*: mARC prioritizes zero-config discovery with explicit team configuration in `team.toml`.
   - *Recommendation*: Support an optional `[token_guard]` table in `team.toml` (e.g., `max_read_lines = 350`), defaulting to zero-config passthrough or reasonable defaults (e.g., 400 lines) when unspecified. Tie metrics into mARC's existing `token_telemetry.py` so teams can observe token savings with `token_telemetry_report.py`.
