# GitHub Copilot Harness Compatibility Tracker

This document tracks compatibility findings and implementation gaps for a
first-class **GitHub Copilot** harness in `harnesses/copilot/marc/`.

## Runtime discoveries (validated locally)

Validated on **GitHub Copilot CLI 1.0.71**:

1. `copilot plugin marketplace --help` confirms marketplace support (`add`,
   `browse`, `list`, `remove`, `update`) and default marketplaces.
2. `copilot plugin install --help` confirms install sources:
   `plugin@marketplace`, `owner/repo`, `owner/repo:path`, and git URL.
3. `copilot skill --help` confirms plugin-provided skills are supported and that
   project skill discovery includes `.github/skills/`, `.agents/skills/`, and
   `.claude/skills/`.
4. `copilot plugin list` output is plain text (example:
   `marc@nexaduo (v0.16.6)`), and `--json` is not documented in help.
5. Session env check showed `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PROJECT_DIR` unset in
   Copilot runtime; only Copilot-specific vars like `COPILOT_CLI` and
   `COPILOT_AGENT_SESSION_ID` were present.

---

## Harness Feature Matrix

| Feature / Component | Claude Code (current mARC behavior) | GitHub Copilot finding | Compatibility Status | Action Needed / Notes |
| :--- | :--- | :--- | :---: | :--- |
| **Plugin marketplace + install** | Uses marketplace metadata + `plugin install marc@nexaduo`. | Native `copilot plugin marketplace` + `copilot plugin install` are supported. | **Highly Compatible** | Add a dedicated Copilot harness directory and install target (`owner/repo:path`). |
| **Skills (`SKILL.md`)** | `/marc:init` and `/marc:tech-lead` from `skills/*/SKILL.md`. | Copilot supports plugin skills and project skill discovery. | **Highly Compatible** | Compile `core/skills/*` into `harnesses/copilot/marc/skills/`. |
| **Specialist agents** | `agents/*.md` dispatched by `@techlead`. | Copilot runtime exposes plugin agents as custom agent types (e.g. `marc:engineer`). | **Partially Compatible** | Compile `core/agents/*`; keep dispatch mapping Copilot-specific. |
| **Subagent dispatch API** | Uses Claude `Agent` tool (`subagent_type`, `run_in_background`). | Copilot does not expose Claude `Agent` schema. Dispatch must use Copilot task/custom-agent model. | **Requires Adaptation** | Add Copilot-specific dispatch instructions in compiled `skills/tech-lead/SKILL.md` using Copilot task-agent calls. |
| **`/marc:init` plugin-id detection** | Uses `claude plugin list --json` + `jq` to detect `<plugin>@<marketplace>`. | `copilot plugin list` is human-readable text in current validation (no documented JSON mode). | **Incompatible As-Is** | Replace JSON parsing with Copilot-compatible parsing or explicit user confirmation path. |
| **Durable settings target** | Writes `enabledPlugins` in `.claude/settings.json`. | Copilot repo settings convention is `.github/copilot/settings.json`. | **Requires Adaptation** | Update init flow for Copilot settings path and merge semantics. |
| **Hooks config + runtime paths** | Claude hooks + `CLAUDE_*` env assumptions. | Claude env vars are absent in Copilot runtime. | **Incompatible As-Is** | Add Copilot-native hooks and replace env/path assumptions with Copilot-safe resolution. |
| **Aux scripts (`scripts/*.py`)** | `board_reconcile.py`, `release_verify.py`, `token_sentinel.py`. | Copilot can run shell/python commands in-session. | **Compatible** | Reuse scripts; wire with Copilot harness paths/env conventions. |

---

## Load-bearing mappings for implementation

### 1. Dispatch mapping (`@techlead`)

Current Claude dispatch block is tied to the `Agent` tool. For Copilot, the
compiled skill must instruct dispatch via Copilot task/custom-agent calls (for
example, `marc:engineer`, `marc:sre`, `marc:design`, `marc:security`,
`marc:review`, `marc:research`) with background-friendly behavior.

### 2. Init/settings mapping (`/marc:init`)

Current init logic assumes:

- `claude plugin list --json`, and
- `.claude/settings.json`.

Copilot mapping must use:

- `copilot plugin ...` command family, and
- `.github/copilot/settings.json` for `enabledPlugins`.

### 3. Runtime path/env mapping

Any command snippets using `CLAUDE_PLUGIN_ROOT` / `CLAUDE_PROJECT_DIR` must be
reworked for Copilot-compatible path discovery and safe fallback behavior.

---

## Roadmap to Copilot harness parity

- [x] Create Copilot compatibility tracker (`harnesses/copilot/marc/COMPATIBILITY.md`)
- [ ] Add Copilot harness scaffold (`plugin.json`, `compile.json`, `skills/`, `agents/`, and shared `scripts/` linkage)
- [ ] Compile Copilot output from `core/` into `harnesses/copilot/marc/`
- [ ] Implement Copilot-native dispatch instructions in `skills/tech-lead/SKILL.md`
- [ ] Implement Copilot-native `/marc:init` settings flow for `.github/copilot/settings.json`
- [ ] Implement Copilot-native plugin-id detection (without assuming `--json`)
- [ ] Add Copilot-native hooks configuration and runtime path/env wiring
- [ ] Add CI gates for Copilot harness structure, compile drift, and parity checks

---

## Scope note

This tracker is specific to **GitHub Copilot**. Antigravity tracking remains at:
[`harnesses/antigravity/marc/COMPATIBILITY.md`](../../antigravity/marc/COMPATIBILITY.md).
