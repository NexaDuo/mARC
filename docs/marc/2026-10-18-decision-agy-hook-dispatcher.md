# Decision record: Antigravity hooks run through a plain-word dispatcher

- **Type:** decision record
- **Date:** 2026-10-18
- **Attribution:** operator, on
  [issue #343](https://github.com/NexaDuo/mARC/issues/343), from a probe of the
  real host. It supersedes the
  [2026-10-04 brief](2026-10-04-brief-hook-architecture.md) and PR #342.
- **Status:** accepted, implemented in PR #363

## Context

mARC compiles one hook catalog (`core/hooks/hooks.spec.json`) into each
harness's native hooks file. For Antigravity, the compiled commands embedded
shell snippets that referenced `${AGY_PLUGIN_ROOT}` and `${AGY_PROJECT_DIR}`.
PR #342 tried to get them past Windows `cmd.exe` by base64-encoding them. The
2026-10-04 brief recommended wrapper binaries in general, without checking how
Antigravity actually runs a hook.

## What the host does

The evidence comes from two sources:
- **A probe:** a throwaway plugin installed into agy 1.3.5 on Windows and
  agy 1.3.2 on Linux, run with headless prompts, with the hook stdin,
  environment, working directory and the agy log inspected.
- **The agy binary:** the hook contract is documented inside it (section
  "Input/Output Contract").

| Fact | Evidence |
|---|---|
| `command` runs via `sh -c` on Unix and `cmd /c "<command>"` on Windows. Inner double quotes reach cmd.exe as `\"`, which it does not understand. | Probe (`%CMDCMDLINE%`, cmd errors on a quoted interpreter path). Embedded docs. |
| The working directory is the folder holding `hooks.json`, the plugin root. | Probe (both OSes). Embedded docs. |
| `AGY_PLUGIN_ROOT`/`AGY_PROJECT_DIR` are not set, and `${...}` is not substituted. | Probe (both OSes). |
| The workspace arrives only in the stdin JSON, as `workspacePaths`. `OLDPWD` is inherited from whatever launched agy. | Probe. Embedded docs ("Common Input Fields"). |
| stdout must be one JSON object, shaped per event. Plain text fails with "failed to unmarshal result". | Probe (agy log, both OSes). Embedded docs. |
| `SessionStart` fires once per conversation. `PreInvocation` fires before every model call. | Embedded docs. |
| On Windows, `bash` may resolve to WSL's `bash.exe`. It runs with the working directory translated (`/mnt/c/...`) and with `wslpath` available. | Probe. |

Because stdout must be JSON, no mARC hook output had ever reached the model
on Antigravity, on any OS: the plain text was discarded.

## Decision

- **The command:** every `hooks.json` command is a fixed word list,
  `bash hooks/agy-hook.sh <id> <Event>`. It has no quote, `$`, pipe or
  redirection, so `sh -c` and `cmd /c` pass it through unchanged.
- **The dispatcher (`hooks/agy-hook.sh`):**
  - rebuilds `AGY_PLUGIN_ROOT` (from the working directory) and
    `AGY_PROJECT_DIR` (from `workspacePaths[0]`, with a Windows path
    converted by `cygpath` or `wslpath`);
  - runs the hook body `hooks/agy/<id>.sh` with the same stdin;
  - wraps the body's text in the JSON the event expects (`injectSteps` for
    `SessionStart`/`PreInvocation`, `{}` otherwise);
  - always exits 0.
- **The bodies:** they stay generated from the shared catalog, so there is
  still one source of truth, with no payload encoding.
- **Event mapping:** session-start hooks map to `SessionStart`.
- **The test:** `scripts/test_agy_hooks.py` runs every hook under this
  contract, without a model call. It runs in CI against both the source and
  the installed copy.

## Alternatives considered

- **Base64 in the JSON (PR #342):** it still needs quotes for `bash -c`, and
  those quotes break under cmd.exe with Git Bash. It also does not fix the
  JSON-output requirement. Rejected.
- **A Python or Node shim as the command:** the interpreter name varies
  (`python3`, `python`, `py`, or none at all), and Windows has no shebang
  handling. `bash` is already a prerequisite of every mARC hook body. Rejected
  for now.
- **A compiled wrapper binary:** this is what the 2026-10-04 brief
  recommended. It would add a per-OS build and signing to a plugin that
  ships text files. It is not needed once the command is a plain word list.
  Rejected.

## Consequences

- Windows works with Git Bash or WSL `bash` first on PATH. With no `bash` at
  all, agy logs a failed hook and the turn continues.
- Antigravity's `PostToolUse` contract has no context channel, so post-tool
  advisories reach agy's log, not the model.
- SKILL.md references of the form `${AGY_PLUGIN_ROOT:-.}/scripts/...` rest on
  the same wrong assumption. They are tracked separately.
