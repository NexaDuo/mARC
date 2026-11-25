<!-- mARC :: Multi-Agent Relay Control -->

```
                     █████╗  ██████╗   ██████╗
                    ██╔══██╗ ██╔══██╗ ██╔════╝
        ██████████╗ ███████║ ██████╔╝ ██║
        ██║ ██║ ██║ ██╔══██║ ██╔══██╗ ██║
        ██║ ██║ ██║ ██║  ██║ ██║  ██║ ╚██████╗
        ╚═╝ ╚═╝ ╚═╝ ╚═╝  ╚═╝ ╚═╝  ╚═╝  ╚═════╝
   ▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖▄▖
        m u l t i · a g e n t · r e l a y · c o n t r o l
   ▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘▀▘
```

[![version](https://img.shields.io/github/v/release/NexaDuo/mARC?label=version&color=ff69b4)](https://github.com/NexaDuo/mARC/releases/latest)
[![CI](https://github.com/NexaDuo/mARC/actions/workflows/ci.yml/badge.svg)](https://github.com/NexaDuo/mARC/actions/workflows/ci.yml)
[![license: MIT](https://img.shields.io/github/license/NexaDuo/mARC?color=blueviolet)](LICENSE)
[![site](https://img.shields.io/badge/site-marc.nexaduo.com-00e5ff)](https://marc.nexaduo.com)
[![Tokens Saved (Last Release)](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/NexaDuo/mARC/main/docs/marc/telemetry-badge.json)](https://github.com/NexaDuo/mARC/blob/main/docs/marc/telemetry.md)

```
*** Now talking in #marc
*** Topic: turn a discussion into tracked, delegated, shipped work
--> `@techlead` has been given channel operator status
    `@techlead`: specialists, standby. i'll convene you when there's work.
    `@dev` `@sre` `@design` `@sec` `@rev` `@research`: ready.
```

**mARC (Multi-Agent Relay Control)** packages a full software-delivery agent team, dressed in a retro/vaporwave **IRC** console aesthetic. Rather than building separate agent systems for every platform, mARC defines a single core team in `core/` and compiles it into plugins for different developer tools, which we call **harnesses**. Today, mARC ships four harnesses: Claude Code, OpenAI Codex, Google Antigravity, and GitHub Copilot CLI. It relies on the host tool to run and isolate subagents, providing a consistent team that you install once and reuse across your projects.

## Install

Prerequisites: Node.js 20+ and your harness CLIs on PATH. For Antigravity,
use its [CLI installer](https://antigravity.google/cli/) first.

```bash
npx @nexaduo/marc install
```

The launcher detects Claude Code, Copilot, Codex and Antigravity. It runs each
harness's own plugin CLI and never writes configuration itself. Every command
is shown before it runs.

| Command | Purpose |
|---|---|
| `npx @nexaduo/marc install` | Install on detected harnesses |
| `npx @nexaduo/marc update` | Update installed plugins to the launcher's release |
| `npx @nexaduo/marc doctor` | Check versions and marketplace pointers (read-only) |
| `npx @nexaduo/marc uninstall` | Remove plugins, keeping marketplaces |

Flags: `--harness <claude|copilot|codex|agy>` selects a harness (repeatable);
`--scope <user|project|local>` selects a Claude scope only; `--dry-run` previews;
`--yes` confirms mutations in scripts. Claude update previews list the actual
settings paths that its CLI will edit. Antigravity uses a temporary,
SHA-verified clone that is deleted afterwards.

The npm package embeds one release: `v26.11.25.4` maps to npm `26.1125.4`.
Antigravity installs exactly that tag; Claude Code, Copilot and Codex follow
the marketplace catalog, which points at the latest release. Use the latest
package to keep every harness in lockstep.
After installation, invoke `/marc:tech-lead` in your harness.

### Codex compatibility

Initial support is verified with CLI 0.156.1 for marketplace installation and
bundled-helper execution from a fresh consumer without `team.toml`. Writers use
`codex exec --worktree --sandbox workspace-write`. All read-only roles route to
Claude Code and fail closed if unavailable: a filesystem sandbox alone does not
prove tool restrictions. Native agent TOMLs are templates; automatic plugin-agent
discovery is not yet verified. Startup commands are compiled, but real hook
trust/event delivery still needs validation. Codex `read-guard` is disabled and
telemetry/sentinel tools report **unavailable** without reading transcripts or
writing telemetry. To intentionally process Claude data, pass
`--harness claude-code`. Detailed tech-lead procedures load on demand from
references to keep the initial skill prompt small.

## The team: leaders and a shared specialist bench

Think of your project as an IRC channel. **`@techlead`** holds channel-operator status. It listens to the discussion, compiles it into well-specified, tracked work on your GitHub Project board, and then pings the right specialist to do it.

| handle       | role                 | pings for                                            |
|--------------|----------------------|------------------------------------------------------|
| `@eng-director` | team coordination | dependencies, operational recovery, delegated resource limits |
| `@techlead`  | channel operator     | convene, spec, record on the board, dispatch, track  |
| `@dev`       | engineer             | app/service code, IaC, deploy scripts, schema, tests |
| `@sre`       | reliability          | deploys, observability, incidents, backups/DR, cost  |
| `@design`    | front-end            | UI screens and overall web flows                     |
| `@sec`       | security (read-only) | pre-merge diff review; one half of the mandatory merge gate |
| `@rev`       | review (read-only)   | pre-merge correctness review; the other half of the mandatory merge gate |
| `@research`  | researcher (read-only) | external evidence (such as benchmarks or documentation) as a cited brief |
| `@arch`      | architect (read-only) | one hard decision, escalated to on explicit triggers; advises in a `## marc:arch decision` comment |

`@techlead` and `@eng-director` are leadership **skills** you invoke in a
session. The specialists are subagents dispatched for individual tasks. Start
with `/marc:tech-lead` for project delivery; add a director when several teams
need coordination. Each techlead keeps ownership of its work and reviews.

## Coordinate several teams

Open a separate session in your coordination workspace and invoke:

```
/marc:eng-director
```

The workspace can be private and does not need to be a Git repository. Tell the
director which teams and repositories it coordinates, where their queues and
checkpoints live, and which routine operations you delegate. Several teams can
work in the same repository or across repositories and harnesses.

For example: “Coordinate the API and client teams. Read their queues and recent
owner decisions, identify dependencies, and report verified progress. Ask before
changing session or resource settings.”

The director reads the work queue before declaring a team idle, routes decisions
and dependencies, and checks evidence before reporting completion. With your
existing local tooling and authorization, it can reconcile watchers and
schedules, resume sessions from checkpoints, and manage shared resource limits.
Machine paths, session mappings, schedules and quotas stay in your private
workspace; the skill carries the reusable procedure.

Invoking the skill starts a leadership conversation. Session wake-up, restart,
context measurements and monitoring depend on the host tools and local adapters;
installing mARC does not create a background supervisor. Delegated operations
remain subject to their checks, permissions and the repository's review gates.

See the [director skill](core/skills/eng-director/SKILL.md),
[operating contract](core/skills/eng-director/references/operating-contract.md)
and [recovery procedures](core/skills/eng-director/references/operations.md).

## Generic by design: repository configuration

The team carries no hardcoded stack facts. When running in a repository, the agents discover details dynamically at runtime:

- They read the repository's `AGENTS.md` or `CLAUDE.md` to learn about the architecture and any lessons from previous issues.
- They check the repository configuration file (`.agents/team.toml`) for settings like the GitHub organization, repository name, project number, source paths, and validation commands. See [`docs/team.toml.example`](docs/team.toml.example) for an example.
- If the configuration file is missing, `@techlead` automatically queries GitHub using `gh` commands to locate the correct repository and project board.

A `SessionStart` hook outputs the active `team.toml` configuration into the context at the beginning of each session. If the configuration is missing, it prints a friendly note. If it finds a legacy `team.config` file, it prompts you to migrate by running `/marc:init`.

## Receipts: this repo runs on mARC

mARC is built by the team it ships. Every spec, dispatch, security review, and decision that produced this repo is public, so you can audit the process before trusting it:

- the [Project board](https://github.com/orgs/NexaDuo/projects/2), where `@techlead` records and tracks every task,
- the [issues](https://github.com/NexaDuo/mARC/issues?q=is%3Aissue) and [pull requests](https://github.com/NexaDuo/mARC/pulls?q=is%3Apr), with inline spec details and security reviews,
- [`docs/marc/`](docs/marc/), the durable decision records and research briefs.

## For teams

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

## Update

```bash
npx @nexaduo/marc update
```

The launcher updates all detected harnesses to its embedded release and preserves
disabled plugins. Use `npx @nexaduo/marc doctor` to check for drift or legacy
marketplace pins; it exits 1 if either needs attention and prints guidance.

A `SessionStart` hook checks for newer versions once per session and prints an
update reminder. Offline checks are silent and do not block the session.

## Bind mARC to a repo (optional but recommended)

Run **`/marc:init`** in the consuming repo. It scaffolds the repository configuration (`.agents/team.toml`). This config pins the GitHub org and repo, the Project number, key source paths, and the validation command so `@techlead` and the specialists do not have to guess. It shows you every file before writing anything. If you prefer to set it up by hand, copy [`docs/team.toml.example`](docs/team.toml.example) and fill it in. Note that a repository's local `.agents/` configuration folder overrides the plugin, which in turn overrides user config. For backward compatibility, a pre-existing `.claude/team.toml` is still read as a fallback if `.agents/team.toml` is absent (harness-dependent legacy path; see each harness's `COMPATIBILITY.md`).

## Harness Architecture & Compatibility

To support multiple host tools, mARC uses a harness-based architecture. A single core team of agents is defined under `core/` and compiled into platform-specific plugins located in the `harnesses/` directory.

Currently, mARC ships four harnesses: Claude Code (plugin under `harnesses/claude-code/marc/`), OpenAI Codex (plugin under `harnesses/codex/marc/`), Google Antigravity (plugin under `harnesses/antigravity/marc/`), and GitHub Copilot CLI (plugin under `harnesses/copilot/marc/`).

Because each host tool provides different agent execution APIs, the subagent dispatch mechanism varies between harnesses. Claude Code uses the native `Agent` tool to dispatch subagents, Codex uses its native agent dispatch, Google Antigravity uses `invoke_subagent`, and Copilot uses the `task` tool with plugin custom agents (`marc:*`).

The leader skill `@techlead` dynamically inspects the available tools and maps its requests to the appropriate dispatch tool. For detailed mapping notes, refer to:
- [Google Antigravity compatibility tracker](harnesses/antigravity/marc/COMPATIBILITY.md)
- [GitHub Copilot compatibility tracker](harnesses/copilot/marc/COMPATIBILITY.md)

## Layout

```
core/                            # core specialist agent templates and leader skills
harnesses/
  claude-code/
    marc/                        # compiled Claude Code plugin
      .claude-plugin/plugin.json # plugin manifest
      skills/eng-director/       # multi-team coordination and operational recovery
      skills/tech-lead/          # `@techlead` leader skill (/marc:tech-lead)
      skills/init/               # `/marc:init`, opt-in per-repo onboarding
      agents/                    # `@dev`, `@sre`, `@design`, `@sec`, `@research` shared specialist bench
      hooks/hooks.json           # SessionStart hook to inject `.agents/team.toml`
  antigravity/
    marc/                        # compiled Google Antigravity plugin
      plugin.json                # plugin manifest
      skills/                    # symlinked leader skills (e.g. `/marc:tech-lead`, `/marc:init`)
      agents/                    # symlinked specialist agents
      COMPATIBILITY.md           # compatibility tracker for Google Antigravity
  copilot/
    marc/                        # compiled GitHub Copilot CLI plugin
      plugin.json                # Copilot plugin manifest
      skills/eng-director/       # multi-team coordination and operational recovery
      skills/tech-lead/          # `@techlead` leader skill
      skills/init/               # `/marc:init`, Copilot config flow
      agents/                    # `@dev`, `@sre`, `@design`, `@sec`, `@research` bench
      hooks/hooks.json           # Copilot `version: 1` hooks config
      COMPATIBILITY.md           # compatibility tracker for GitHub Copilot
  codex/
    marc/                        # compiled OpenAI Codex plugin
      plugin.json                # Codex plugin manifest
      skills/eng-director/       # multi-team coordination and operational recovery
      skills/tech-lead/          # `@techlead` leader skill
      agents/                    # Codex specialist agent definitions
      hooks/hooks.json           # Codex hooks configuration
docs/
  ARCHITECTURE.md                # growth model covering the different roles and harnesses
  marc/                          # durable team artifacts like decision records and research briefs
  team.toml.example
launcher/                        # npx launcher delegating to native plugin CLIs
```

The plugin is structured so that the core logic under `core/` compiles into platform-specific configurations in the `harnesses/` directory. This allows the project to grow sideways into new harnesses or upward into further leader skills like `founder` without restructuring the repository. Detailed growth plans are documented in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Releases

Merging a version bump to `main` releases mARC automatically through
`release.yml`: tests, an Actions-created annotated tag, GitHub Release notes,
and trusted npm publishing. Humans and agents do not create `v*` tags. To batch
several PRs into one daily version, bump only in the last PR. After merge, verify
with `python3 core/scripts/release_verify.py`; retry missing surfaces with
`workflow_dispatch` on `main`. See the [release decision and bootstrap](docs/marc/2026-11-25-decision-npx-launcher.md#release-on-merge-user-decision-2026-11-25).

## License

MIT, see [LICENSE](LICENSE).

```
*** `@techlead` sets mode +v on your next idea
```
