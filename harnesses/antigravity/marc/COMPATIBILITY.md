# Google Antigravity (agy) Harness Compatibility Tracker

This document tracks the compatibility items, architectural gaps, and mapping rules for porting the mARC agent team to the **Google Antigravity CLI (`agy`)** harness. It serves as a living record to guide future development and review compatibility iterations.

## Overview

The mARC team is designed to be harness-agnostic. The core specialist prompts ([AGENTS.md](../../../AGENTS.md)) and leader skills ([SKILL.md](../../claude-code/marc/skills/tech-lead/SKILL.md)) contain role behaviors and boundaries rather than vendor-specific runtime logic. However, the executing harness dictates manifest schemas, slash command formats, subagent execution APIs, and local file paths.

---

## Harness Feature Matrix

| Feature / Component | Claude Code Behavior | Google Antigravity (`agy`) Behavior | Compatibility Status | Action Needed / Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Skill Definition (`SKILL.md`)** | Progressive disclosure from `skills/<name>/SKILL.md` (YAML frontmatter + markdown instructions). | Natively supports `skills/<name>/SKILL.md` with identical YAML frontmatter metadata. | **100% Compatible** | None. Skills can be symlinked directly to share logic. |
| **Subagent Dispatch API** | Calls the native `Agent` tool (`subagent_type`, `prompt`, `run_in_background`). | Calls the native `invoke_subagent` tool (array of subagents with `TypeName`, `Role`, `Prompt`). | **Requires Adaptation** | Abstract the dispatch command in `@techlead`'s prompt based on the detected harness/tools. |
| **Plugin Manifest** | Manifest defined at `.claude-plugin/plugin.json`. | Manifest defined at `plugin.json` in the plugin root. | **Partially Compatible** | Maintained as a sibling file under the specific harness folder. |
| **Execution Hooks** | Defined in `hooks/hooks.json` (trigger: `SessionStart`). Runs shell commands. | Defined in `hooks/hooks.json` or manifest (triggers: `SessionStart`, `PreInvocation`, etc.). | **Highly Compatible** | Adjust environment variables (e.g., `CLAUDE_PROJECT_DIR` $\rightarrow$ `AGY_PROJECT_DIR`) in scripts. |
| **Local Config Path** | Discovers workspace configurations under `.claude/` (e.g., [team.toml](../../../.claude/team.toml)). | Discovers workspace configurations under `.agents/` (e.g., `.agents/team.toml`). | **Requires Dual-Support** | Update config search scripts to fall back to `.agents/` when `.claude/` is missing. |
| **Bash Helper Scripts** | Runs scripts inside `scripts/` via terminal command execution. | Runs identical scripts inside `scripts/` via terminal command execution. | **100% Compatible** | Ensure required CLI utilities (`gh`, `jq`) are present on the user's system. |
| **Rich Output / Artifacts** | Standard Markdown console rendering. | HTML Auxiliary Pane supporting visual Artifacts, carousels, and image editing. | **Upgrade (Backward Compatible)** | `@techlead` can optionally write visual status reports to `<appDataDir>/brain/<conversation-id>`. |

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

In Google Antigravity, the equivalent is:
```json
{
  "name": "invoke_subagent",
  "arguments": {
    "Subagents": [
      {
        "TypeName": "self",
        "Role": "engineer",
        "Prompt": "...",
        "Workspace": "inherit"
      }
    ]
  }
}
```

**Resolution Strategy:** `@techlead` must inspect the available tool schemas and dynamically use `invoke_subagent` if the `Agent` tool is absent.

### 2. Dual Path Resolution

To ensure zero-config scripts function identically under both platforms, scripts must resolve configurations dynamically:
```bash
# Locate the active workspace configuration
CFG="${CLAUDE_PROJECT_DIR:-.}/.claude/team.toml"
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
- [ ] Establish symlinks for shared assets (`skills/`, `agents/`, `hooks/`)
- [ ] Implement dual `.claude/` and `.agents/` directory lookup in all scripts
- [ ] Add Antigravity validation and structural gates to the CI pipeline (`.github/workflows/ci.yml`)
- [ ] Implement conditional tool-calling logic (Claude `Agent` vs Antigravity `invoke_subagent`) in `@techlead`'s prompt
