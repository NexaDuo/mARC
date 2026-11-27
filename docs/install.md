# Install, update and bind to a repo

Full install reference: commands, versions, updating, repo binding and per-repo declarative install.

Back to the [README](../README.md).

## Install

Prerequisites: Node.js 20+ and your harness CLIs on PATH. For Antigravity,
use its [CLI installer](https://antigravity.google/cli/) first.

```bash
npx @nexaduo/marc install
```

The launcher detects Claude Code, Copilot, Codex and Antigravity. It runs each
harness's own plugin CLI and never writes configuration itself. Every command
is shown before it runs.


### Commands and flags

| Command | Purpose |
|---|---|
| `npx @nexaduo/marc install` | Install, or update a stale install, on detected harnesses |
| `npx @nexaduo/marc update` | Deprecated alias of `install` |
| `npx @nexaduo/marc doctor` | Check versions and marketplace pointers (read-only) |
| `npx @nexaduo/marc uninstall` | Remove plugins, keeping marketplaces |

Options (details in [`launcher/README.md`](../launcher/README.md)):

- `--yes`: confirm mutations without a prompt (required in scripts)
- `--dry-run`: print the plan and change nothing
- `--harness <claude|copilot|codex|agy>`: act on one harness (repeatable)
- `--scope <user|project|local>`: Claude scope only
- `--version <tag>`: must equal the launcher's embedded release
- `--force`: replace a nexaduo marketplace registered from another source or a dev clone
- `--all-scopes`: Claude only; also update every stale `project`/`local` install whose directory still exists

```bash
npx @nexaduo/marc install --force
```

`install` also updates a stale install, and `update` is a deprecated alias for it.
A local or unverifiable source is replaced only after you type `replace` in a
terminal. Antigravity uses a temporary, SHA-verified clone that is deleted afterwards.

### Versions and lockstep

Each npm package version maps to one release tag. For example, `v26.12.1` maps to npm
`26.1201.0` (the live version is in `VERSION`).
Antigravity installs exactly the tag its package version maps to; Claude Code, Copilot and Codex follow
the marketplace catalog, which points at the latest release. Use the latest
package to keep every harness in lockstep.
After installation, invoke `/marc:tech-lead` in your harness.

## Update

```bash
npx @nexaduo/marc install
```

`install` converges: it installs when missing, updates a stale install (and a
marketplace catalog that lags behind) and does nothing when everything is current.
`update` is a deprecated alias with identical behavior. Claude Code acts on one
scope (`--scope`, default `user`). A stale `project` or `local` install elsewhere is
offered in a terminal as a multi-select (entries whose directory is gone are marked
`missing` and never touched), and `npx @nexaduo/marc install --all-scopes` updates every
existing one. Without a terminal choice or `--all-scopes`, it is only reported, never
written. If no nexaduo marketplace is registered, `install` cannot fix an old plugin:
run `claude plugin marketplace add NexaDuo/mARC` first, then `install` again. Antigravity reinstalls its
embedded tag, while Claude Code, Copilot and Codex follow the marketplace catalog.
Disabled plugins are preserved. Use `npx @nexaduo/marc doctor` to check for drift: a
harness that is only older is `STALE` (run `install`, or `install --all-scopes` when a
project or local row with an existing directory is the stale one; a row whose directory is
gone is reported separately and not fixable by install, tracked in #470), anything else is `FAILED`, and
it exits non-zero in both cases. See the [launcher README](../launcher/README.md) for
details.

A `SessionStart` hook checks for newer versions once per session and prints an
update reminder. Offline checks are silent and do not block the session.

## Bind mARC to a repo

Run **`/marc:init`** in the consuming repo. It scaffolds the repository configuration (`.agents/team.toml`). This config pins the GitHub org and repo, the Project number, key source paths, and the validation command so `@techlead` and the specialists do not have to guess. It shows you every file before writing anything. If you prefer to set it up by hand, copy [`team.toml.example`](team.toml.example) and fill it in. Note that a repository's local `.agents/` configuration folder overrides the plugin, which in turn overrides user config. For backward compatibility, a pre-existing `.claude/team.toml` is still read as a fallback if `.agents/team.toml` is absent (harness-dependent legacy path; see each harness's `COMPATIBILITY.md`).

## Repository configuration

The team carries no hardcoded stack facts. When running in a repository, the agents discover details dynamically at runtime:

- They read the repository's `AGENTS.md` or `CLAUDE.md` to learn about the architecture and any lessons from previous issues.
- They check the repository configuration file (`.agents/team.toml`) for settings like the GitHub organization, repository name, project number, source paths, and validation commands. See [`team.toml.example`](team.toml.example) for an example.
- If the configuration file is missing, `@techlead` automatically queries GitHub using `gh` commands to locate the correct repository and project board.

A `SessionStart` hook outputs the active `team.toml` configuration into the context at the beginning of each session. If the configuration is missing, it prints a friendly note. If it finds a legacy `team.config` file, it prompts you to migrate by running `/marc:init`.

## Declarative per-repo install (Claude Code)

For a zero-command, per-repo Claude Code install, declare the marketplace and
plugin in the project's `.claude/settings.json`:

```json
{
  "extraKnownMarketplaces": {
    "nexaduo": {"source": {"source": "github", "repo": "NexaDuo/mARC"}}
  },
  "enabledPlugins": {"marc@nexaduo": true}
}
```

When collaborators trust the folder, Claude Code prompts them to install the
declared plugin and loads it in that repo. This declarative team setup is
separate from running the launcher on each machine.
