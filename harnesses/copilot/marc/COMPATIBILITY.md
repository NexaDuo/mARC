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
| **Skills (`SKILL.md`)** | `/marc:init` and `/marc:tech-lead` from `skills/*/SKILL.md`. | Copilot supports plugin skills and project skill discovery. | **Implemented** | Compiled output now ships under `harnesses/copilot/marc/skills/`. |
| **Specialist agents** | `agents/*.md` dispatched by `@techlead`. | Copilot runtime exposes plugin agents as custom agent types (e.g. `marc:engineer`). | **Implemented** | Compiled output now ships under `harnesses/copilot/marc/agents/`. |
| **Subagent dispatch API** | Uses Claude `Agent` tool (`subagent_type`, `run_in_background`). | Copilot does not expose Claude `Agent` schema. Dispatch uses Copilot task/custom-agent model. | **Implemented** | Copilot `tech-lead` compile mapping now dispatches via task tool to `marc:*` agents. |
| **`/marc:init` plugin-id detection** | Uses `claude plugin list --json` + `jq` to detect `<plugin>@<marketplace>`. | `copilot plugin list` is human-readable text in current validation (no documented JSON mode). | **Implemented** | Copilot mapping now shims `copilot plugin list` into JSON-compatible output for existing merge logic. |
| **Durable settings target** | Writes `enabledPlugins` in `.claude/settings.json`. | Copilot repo settings convention is `.github/copilot/settings.json`. | **Implemented** | Copilot `init` compile mapping now targets `.github/copilot/settings.json`. |
| **Hooks config + runtime paths** | Claude hooks + `CLAUDE_*` env assumptions. | Claude env vars are absent in Copilot runtime. | **Implemented** | Copilot harness now ships `hooks/hooks.json` in Copilot `version: 1` schema with Copilot env/path wiring. |
| **Aux scripts (`scripts/*.py`)** | `board.py` (`board_reconcile.py` kept as a deprecated shim for one release), `release_verify.py`, `token_sentinel.py`. | Copilot can run shell/python commands in-session; no install-root env var to reference them directly (see finding 5 above). | **Compatible** | Ship physical copies under `harnesses/copilot/marc/scripts/` (self-contained, no cross-harness symlink) and additionally sync them into the writable `COPILOT_PLUGIN_DATA` dir at session start via a `sessionStart` hook with a best-effort name/repo sanity check — see "Script-sync sanity check" below. |

---

## Load-bearing mappings for implementation

### 1. Dispatch mapping (`@techlead`)

Current Claude dispatch block is tied to the `Agent` tool. For Copilot, the
compiled skill must instruct dispatch via Copilot task/custom-agent calls (for
example, `marc:engineer`, `marc:sre`, `marc:design`, `marc:security`,
`marc:research`) with background-friendly behavior.

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

### 4. Script-sync sanity check (`hooks/hooks.json` sessionStart)

Copilot exposes no install-root env var equivalent to `CLAUDE_PLUGIN_ROOT`
(finding 5, above) — only a writable *data* dir (`COPILOT_PLUGIN_DATA`), which
`compile.json`'s `plugin_root_env` points at. Because the compiled skill
commands run out of that data dir, the `scripts/*.py` files have to be synced
into it before use. Locating "our own" install to copy from still requires a
`find` under `$HOME/.copilot/installed-plugins`, since Copilot gives us no
direct reference to it.

That `find` previously trusted whatever `*/marc/plugin.json` it saw first
(`head -n1`, non-deterministic ordering) and copied `.py` files out of that
directory — a plugin directory named `marc` from an untrusted source (a
typosquat) would have its code adopted and later executed by the `postToolUse`
hook and the tech-lead skill, with zero check at all. Improved by adding a
sanity check: each candidate's `plugin.json` is parsed and only used if
`.name == "marc"` **and** `.repository`/`.homepage` reference `NexaDuo/mARC` —
non-matching candidates are skipped, and candidates are sorted first so the
outcome is deterministic. No matching candidate is a silent no-op (nothing to
sync yet, not an error); a mismatched candidate is skipped rather than adopted.

This is a best-effort reduction of accidental/first-match adoption, not an
authentication or anti-typosquat boundary: `.name`/`.repository`/`.homepage`
are self-declared by the candidate's own `plugin.json`, so a targeted
typosquatter can simply write matching values into their own file and pass the
check. Copilot exposes no install-root env var (or other trusted identity
signal) for plugins, so no stronger mechanism is available at this layer.

`scripts/` itself is no longer a symlink escaping to
`harnesses/claude-code/marc/scripts` (that dangles on a
`owner/repo:harnesses/copilot/marc` subpath install, which only checks out the
copilot subtree) — it now ships physical copies in-tree, so the sync above
always has real files to find and copy regardless of install shape.

---

## Roadmap to Copilot harness parity

- [x] Create Copilot compatibility tracker (`harnesses/copilot/marc/COMPATIBILITY.md`)
- [x] Add Copilot harness scaffold (`plugin.json`, `compile.json`, `skills/`, `agents/`, and a self-contained `scripts/` directory — physical copies, not a cross-harness symlink, so a `owner/repo:harnesses/copilot/marc` subpath install is never missing its own scripts)
- [x] Compile Copilot output from `core/` into `harnesses/copilot/marc/`
- [x] Implement Copilot-native dispatch instructions in `skills/tech-lead/SKILL.md`
- [x] Implement Copilot-native `/marc:init` settings flow for `.github/copilot/settings.json`
- [x] Implement Copilot-native plugin-id detection (without assuming `--json`)
- [x] Add Copilot-native hooks configuration and runtime path/env wiring
- [x] Add CI gates for Copilot harness structure, compile drift, and parity checks

---

## Scope note

This tracker is specific to **GitHub Copilot**. Antigravity tracking remains at:
[`harnesses/antigravity/marc/COMPATIBILITY.md`](../../antigravity/marc/COMPATIBILITY.md).
