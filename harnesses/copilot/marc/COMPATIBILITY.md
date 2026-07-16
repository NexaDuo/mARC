# GitHub Copilot Harness Compatibility Tracker

This document tracks compatibility items, gaps, and concrete mapping rules for
porting mARC to a first-class **GitHub Copilot** harness. It is a living record
for implementation and review.

## Overview

mARC is defined once in `core/` and compiled into harness-specific outputs.
The core role intent is harness-agnostic, but runtime mechanics are not:
plugin manifest conventions, hook schema, dispatch APIs, and config paths differ
between Claude Code and Copilot.

This tracker compares what mARC currently uses in the Claude Code harness against
what Copilot supports today, based on docs + runtime checks.

---

## Harness Feature Matrix

| Feature / Component | Claude Code (current mARC behavior) | GitHub Copilot behavior | Compatibility Status | Action Needed / Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Plugin install + marketplace** | Uses `.claude-plugin/marketplace.json` + nested plugin source (`harnesses/claude-code/marc`). | Supports `copilot plugin marketplace add` and `copilot plugin install`; also resolves manifests from `.claude-plugin/plugin.json`. | **Highly Compatible** | Keep marketplace metadata compatible with Copilot plugin reference. |
| **Skills (`SKILL.md`)** | `/marc:init`, `/marc:tech-lead` loaded from `skills/*/SKILL.md`. | Copilot plugins load skills from `skills/*/SKILL.md` and expose them via `/skills`. | **100% Compatible** | No format rewrite required for basic skill loading. |
| **Specialist agents** | Specialist bench exists under `agents/*.md` and is dispatched by `@techlead`. | Plugin agents are loaded and appear as custom agents (`marc:engineer`, `marc:sre`, etc.). | **Partially Compatible** | Runtime dispatch contract differs (see next row). |
| **Subagent dispatch API** | `@techlead` instructions call Claude's `Agent` tool with `subagent_type` + `run_in_background`. | Copilot does not expose Claude's `Agent` schema; uses Copilot task/subagent model. | **Requires Adaptation** | Create Copilot-specific dispatch instructions and tool schema in compiled skill output. |
| **Hooks schema** | Uses Claude-format `hooks/hooks.json` (`SessionStart`, `PostToolUse`, nested `hooks` arrays, `matcher`). | Copilot hooks use `version: 1` schema and different event payload contract. | **Incompatible As-Is** | Provide Copilot-native hooks file/schema for this harness. |
| **Hook runtime env vars** | Scripts reference `${CLAUDE_PLUGIN_ROOT}` / `${CLAUDE_PROJECT_DIR}`. | In runtime checks, these variables are unset in Copilot sessions. | **Incompatible As-Is** | Replace with Copilot-compatible paths/env and safe fallbacks. |
| **`/marc:init` durable enablement step** | Writes `.claude/settings.json` and uses `claude plugin list --json` to detect `<plugin>@<marketplace>`. | Copilot plugin management is `copilot plugin ...`; repo settings convention is `.github/copilot/settings.json`. | **Requires Adaptation** | Add Copilot-specific init flow and settings target path. |
| **Team binding path (`.claude/team.toml`)** | Read via `${CLAUDE_PROJECT_DIR:-.}/.claude/team.toml`. | Path itself is usable, but current wording/commands are Claude-specific in places. | **Partially Compatible** | Keep TOML model, but remove Claude-only command assumptions. |
| **Aux scripts (`scripts/*.py`)** | Invoked by skills for board reconcile/release verify/token sentinel. | Copilot can run shell commands and execute same scripts. | **100% Compatible** | Reuse scripts; adjust only harness-specific env/path wiring. |
| **Frontmatter fields** | Current files include fields like `handle` used by mARC role prose. | Copilot diagnostics warn that `handle` is unsupported in VS Code agent schema. | **Mostly Compatible** | Treat as non-blocking metadata drift; optionally normalize per harness. |

---

## Runtime Evidence (local validation)

Validated in local environment with **GitHub Copilot CLI 1.0.71**:

1. `copilot plugin install /.../harnesses/claude-code/marc` succeeded.
2. `copilot skill list` showed `init` and `tech-lead` from mARC plugin.
3. Prompting Copilot to list custom agents returned:
   `marc:engineer`, `marc:sre`, `marc:research`, `marc:security`, `marc:design`.
4. Copilot logs confirm repository hooks are loaded in prompt mode, but existing
   Claude hook schema/env assumptions are not safe to reuse unchanged.
5. In-session env check showed `CLAUDE_PLUGIN_ROOT` and `CLAUDE_PROJECT_DIR` unset.

---

## Load-Bearing Architectural Mappings

### 1. Dispatch mapping (must be harness-specific)

Current Claude instruction shape (from `@techlead`) assumes:

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

Copilot harness must emit Copilot-native dispatch instructions/tool calls
for the same intent (specialist role + background behavior), instead of
reusing the Claude `Agent` contract.

### 2. Hook mapping (must be schema-specific)

Current Claude hooks rely on:

- event names and matcher style from Claude plugin format,
- nested `hooks` entries, and
- Claude env vars in command payloads.

Copilot harness must ship a dedicated hooks config using Copilot's
`version: 1` schema and Copilot-compatible runtime variables/paths.

### 3. Init/settings mapping

Current `/marc:init` flow writes `.claude/settings.json` and shells out to
`claude plugin list`. For Copilot, this must be mapped to:

- `copilot plugin` command family, and
- repo-level settings path `.github/copilot/settings.json`
  (`enabledPlugins`, marketplaces as needed).

---

## Roadmap to Copilot Harness Parity

- [x] Create Copilot compatibility tracker (`harnesses/copilot/marc/COMPATIBILITY.md`)
- [ ] Add Copilot plugin manifest (`plugin.json`) for this harness
- [ ] Add Copilot `compile.json` and template placeholders for Copilot-specific values
- [ ] Compile Copilot output from `core/` into `harnesses/copilot/marc/`
- [ ] Implement Copilot-native dispatch instructions in compiled `skills/tech-lead/SKILL.md`
- [ ] Implement Copilot-native `/marc:init` settings flow (`.github/copilot/settings.json`)
- [ ] Add Copilot-native hooks config (`version: 1`) and migrate hook scripts/env wiring
- [ ] Add CI gates for Copilot harness structure, compile drift, and parity checks

---

## Scope note

This tracker is about **GitHub Copilot** compatibility only. Antigravity tracking
remains in:
[`harnesses/antigravity/marc/COMPATIBILITY.md`](../../antigravity/marc/COMPATIBILITY.md).
