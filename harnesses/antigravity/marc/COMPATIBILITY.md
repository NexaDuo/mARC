# Google Antigravity (agy) Harness Compatibility Tracker

This document tracks the compatibility items, architectural gaps, and mapping rules for porting the mARC agent team to the **Google Antigravity CLI (`agy`)** harness. It serves as a living record to guide future development and review compatibility iterations.

## Overview

The mARC team is designed to be harness-agnostic. The core specialist prompts ([AGENTS.md](../../../AGENTS.md)) and leader skills ([SKILL.md](../../claude-code/marc/skills/tech-lead/SKILL.md)) contain role behaviors and boundaries rather than vendor-specific runtime logic. However, the executing harness dictates manifest schemas, slash command formats, subagent execution APIs, and local file paths.

---

## Harness Feature Matrix

| Feature / Component | Claude Code Behavior | Google Antigravity (`agy`) Behavior | Compatibility Status | Action Needed / Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Skill Definition (`SKILL.md`)** | Progressive disclosure from `skills/<name>/SKILL.md` (YAML frontmatter + markdown instructions). | Natively supports `skills/<name>/SKILL.md` with identical YAML frontmatter metadata. | **100% Compatible** | Compiled from `core/` with harness configuration. |
| **Subagent Dispatch API** | Calls the native `Agent` tool (`subagent_type`, `prompt`, `run_in_background`). | Calls the native `invoke_subagent` tool (array of subagents with `TypeName`, `Role`, `Prompt`, `Workspace: "share"`, `Model: "flash"|"pro"|"inherit"`) plus `define_subagent` and `send_message`. | **100% Compatible** | Prompts compiled from `core/` using Antigravity dispatch instructions. |
| **Plugin Manifest** | Manifest defined at `.claude-plugin/plugin.json`. | Manifest defined at `plugin.json` in the plugin root. | **100% Compatible** | Maintained under `harnesses/antigravity/marc/plugin.json`. |
| **Execution Hooks** | Defined in `hooks/hooks.json` (trigger: `SessionStart`). Runs shell commands. | Defined in `hooks.json` at plugin root (triggers: `PreInvocation`, `PostToolUse`, `Stop`) via native Antigravity named dictionary schema. | **100% Compatible** | `scripts/compile_prompts.py` compiles `core/hooks/hooks.spec.json` into Antigravity's native `hooks.json` at plugin root with inline `AGY_PROJECT_DIR`/`AGY_PLUGIN_ROOT` translation (origin: #173, #193, #197). |
| **Local Config Path** | Discovers workspace configurations under `.claude/` (e.g., [team.toml](../../../.claude/team.toml), with `.agents/team.toml` preferred if present). | Discovers workspace configurations under `.agents/` (e.g., `.agents/team.toml`). | **Requires Dual-Support** | Scripts support `.agents/team.toml` with `.claude/` fallback. |
| **Bash Helper Scripts** | Runs scripts inside `scripts/` via terminal command execution. | Runs identical scripts inside `scripts/` via terminal command execution. | **100% Compatible** | Scripts mirrored byte-for-byte from `core/scripts/` (origin: #128). |
| **Rich Output / Artifacts** | Standard Markdown console rendering. | HTML Auxiliary Pane supporting visual Artifacts, carousels, and image editing. | **Upgrade (Backward Compatible)** | `@techlead` can optionally write visual status reports to `<appDataDir>/brain/<conversation-id>`. |
| **Context Management & Compaction** | Native `/compact` command and background auto-compaction (`autoCompactEnabled`). | No native `/compact` or auto-compaction settings. Supports `/context`, `/fork`, `/rewind`, `/clear`, and exposes `context_window` payload on `statusLine`. | **Harness Disparity (Handled by Prompt Compilation)** | Context-size advisory retired (#181); techlead prompt compiles with `compact_action: "start a fresh session"` under Antigravity. No statusline bridge built (#186). |

---

## Load-bearing Architectural Mappings

### 1. Subagent Dispatch Tooling

In Claude Code, the `@techlead` skill dispatches to specialists using:
```json
{
  "name": "Agent",
  "arguments": {
    "subagent_type": "engineer",
    "prompt": "...",
    "run_in_background": true
  }
}
```

In Google Antigravity, the native equivalent is:
```json
{
  "name": "invoke_subagent",
  "arguments": {
    "Subagents": [
      {
        "TypeName": "self",
        "Role": "engineer",
        "Prompt": "...",
        "Workspace": "share",
        "Model": "pro"
      }
    ]
  }
}
```

**Key Antigravity Capabilities:**
- **Workspace Isolation:** `Workspace: "share"` provides isolated workspaces sharing the underlying repository directory (similar to git worktrees) without duplicating storage, preventing parallel writers from clobbering each other.
- **Model Tiers:** Select `Model: "flash"` for fast research and evidence gathering (`@research`), and `Model: "pro"` or `"inherit"` for deep reasoning, code implementation, and reviews (`@dev`, `@sec`, `@rev`, `@sre`).
- **Dynamic Specialization:** Use `define_subagent` (e.g., `enable_write_tools: false`) to enforce read-only tool boundaries for reviewers and researchers.
- **Ongoing Coordination:** Use `send_message` for two-way agent communication and task steering without spawning redundant subagents.

### 2. Dual Path Resolution

To ensure zero-config scripts function identically under both platforms, scripts resolve configurations dynamically:
```bash
# Locate the active workspace configuration
CFG="${CLAUDE_PROJECT_DIR:-.}/.agents/team.toml"
[ ! -f "$CFG" ] && CFG="${AGY_PROJECT_DIR:-$PWD}/.agents/team.toml"
```

---

## Plugin Distribution / Install

**No clone-free / marketplace install exists for third-party plugins (as of
`agy` v1.1.4, 2026-07-21).** `agy plugin install <plugin>@<marketplace>` is a
documented CLI form, but it resolves against a marketplace registry gated to
Google-internal environments (`GetSkillMarketplaceLink is only available in
Google environments`, per binary strings) — any non-built-in marketplace name
(e.g. `marc@nexaduo`) fails with `unknown marketplace: <name>`. `agy plugin
install` only accepts a **local directory path** in practice, and `agy` does
not read mARC's `.claude-plugin/marketplace.json` (that manifest serves
Claude Code and Copilot only). Consequently, installing mARC's Antigravity
harness requires cloning the repo first, then running `agy plugin install
./mARC/harnesses/antigravity/marc` — there is no shorter one-liner today. See
the full research brief:
[`docs/marc/2026-07-21-brief-agy-marketplace-clone-free-install.md`](../../../docs/marc/2026-07-21-brief-agy-marketplace-clone-free-install.md)
([issue #151](https://github.com/NexaDuo/mARC/issues/151)). Re-check this if
Google documents/opens a public marketplace-registration command.

---

## Roadmap to Harness Parity

- [x] Create Antigravity manifest `plugin.json` ([harnesses/antigravity/marc/plugin.json](plugin.json))
- [x] Create `COMPATIBILITY.md` tracker ([harnesses/antigravity/marc/COMPATIBILITY.md](COMPATIBILITY.md))
- [x] Mirror shared assets and scripts (`skills/`, `agents/`, `scripts/`) from `core/` via `scripts/compile_prompts.py`
- [x] ~~Symlink `hooks/` to Claude Code's~~ — reversed on purpose (origin: #170): a bare
      `hooks -> ../../claude-code/marc/hooks` symlink hardcoded `CLAUDE_PLUGIN_ROOT`
      with no per-harness fallback, silently no-op'ing every Antigravity hook. `hooks/`
      is now a real, self-contained, compiled directory per harness
      (`scripts/compile_prompts.py` from `core/hooks/hooks.spec.json`) — do not
      reintroduce the symlink.
- [x] Native Antigravity hook dialect compiler (`hook_dialect: "antigravity"`) targeting `PreInvocation`, `PostToolUse`, `Stop` (origin: #193)
- [x] Subagent orchestration with `Workspace: "share"`, Gemini model tiers, `define_subagent`, and `send_message` (origin: #193)
- [x] Cross-harness script and hooks parity test gates (`test_hooks_parity.py`, `test_script_parity.py`)
- [ ] Implement dual `.claude/` and `.agents/` directory lookup across all shell helpers
- [ ] Add Antigravity validation and structural gates to the CI pipeline (`.github/workflows/ci.yml`)
