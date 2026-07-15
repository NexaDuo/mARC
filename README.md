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

```
*** Now talking in #marc
*** Topic: turn a discussion into tracked, delegated, shipped work
--> `@techlead` has been given channel operator status
    `@techlead`: specialists, standby. i'll convene you when there's work.
    `@dev` `@sre` `@design` `@sec` `@research`: ready.
```

**mARC (Multi-Agent Relay Control)** packages a full software-delivery agent team, dressed in a retro/vaporwave **IRC** console aesthetic. Rather than building separate agent systems for every platform, mARC defines a single core team in `core/` and compiles it into plugins for different developer tools, which we call **harnesses**. Currently, mARC supports both Claude Code and Google Antigravity as first-class harnesses. It relies on the host tool to run and isolate subagents, providing a consistent team that you install once and reuse across your projects.

## Try it in two minutes

### Claude Code

Run these commands inside Claude Code to add the marketplace, install the plugin, and start:

```
/plugin marketplace add NexaDuo/mARC
/plugin install marc@nexaduo
/marc:tech-lead
```

### Google Antigravity

Run these commands in your terminal to install the Google Antigravity CLI, configure your path, install the plugin, and start:

```bash
pip3 install google-antigravity==0.1.6
curl -sSL https://antigravity.google/cli/install.sh | bash
export PATH="$HOME/.local/bin:$PATH"
agy plugin install harnesses/antigravity/marc
agy /marc:tech-lead
```

## The metaphor: one channel, one op, a bench of specialists

Think of your project as an IRC channel. **`@techlead`** holds channel-operator status. It listens to the discussion, compiles it into well-specified, tracked work on your GitHub Project board, and then pings the right specialist to do it.

| handle       | role                 | pings for                                            |
|--------------|----------------------|------------------------------------------------------|
| `@techlead`  | channel operator     | convene, spec, record on the board, dispatch, track  |
| `@dev`       | engineer             | app/service code, IaC, deploy scripts, schema, tests |
| `@sre`       | reliability          | deploys, observability, incidents, backups/DR, cost  |
| `@design`    | front-end            | UI screens and overall web flows                     |
| `@sec`       | security (read-only) | pre-merge diff review, which is the mandatory merge gate |
| `@research`  | researcher (read-only) | external evidence (such as benchmarks or documentation) as a cited brief |

`@techlead` is a skill (`/marc:tech-lead`), while `@dev`, `@sre`, `@design`, `@sec`, and `@research` are subagents that it dispatches. `@techlead` is the first of several planned leader skills, including `founder` and `eng-director`, which will convene the same shared specialist bench.

## Generic by design: repository configuration

The team carries no hardcoded stack facts. When running in a repository, the agents discover details dynamically at runtime:

- They read the repository's `AGENTS.md` or `CLAUDE.md` to learn about the architecture and any lessons from previous issues.
- They check the repository configuration file (either `.claude/team.toml` or `.agents/team.toml`) for settings like the GitHub organization, repository name, project number, source paths, and validation commands. See [`docs/team.toml.example`](docs/team.toml.example) for an example.
- If the configuration file is missing, `@techlead` automatically queries GitHub using `gh` commands to locate the correct repository and project board.

A `SessionStart` hook outputs the active `team.toml` configuration into the context at the beginning of each session. If the configuration is missing, it prints a friendly note. If it finds a legacy `team.config` file, it prompts you to migrate by running `/marc:init`.

## Receipts: this repo runs on mARC

mARC is built by the team it ships. Every spec, dispatch, security review, and decision that produced this repo is public, so you can audit the process before trusting it:

- the [Project board](https://github.com/orgs/NexaDuo/projects/2), where `@techlead` records and tracks every task,
- the [issues](https://github.com/NexaDuo/mARC/issues?q=is%3Aissue) and [pull requests](https://github.com/NexaDuo/mARC/pulls?q=is%3Apr), with inline spec details and security reviews,
- [`docs/marc/`](docs/marc/), the durable decision records and research briefs.

## Install (user-scope)

The quick-start blocks above cover the full installation. If you are using Claude Code, you can also run the installer script. This script adds the marketplace and installs the plugin.

```bash
./install.sh
```

After installation, `@techlead` is available as `/marc:tech-lead` (or `agy /marc:tech-lead` under Google Antigravity) in any repository, and it dispatches the specialist subagents on demand.

## Update

For Claude Code, enabling auto-update for the `nexaduo` marketplace is recommended. When auto-update is active, Claude Code pulls new plugin versions automatically. You can manage this setting by going to `/plugin` and finding the `nexaduo` marketplace.

To update Claude Code manually at any time:

```
claude plugin update marc@nexaduo
```

Or run these commands inside Claude Code:

```
/plugin marketplace update nexaduo
/reload-plugins
```

For Google Antigravity, you can update by re-running the installation command:

```bash
agy plugin install harnesses/antigravity/marc
```

**Update notifications:** mARC ships a `SessionStart` hook that checks whether your installed version is behind the version on `main` once per session. If a new major or minor version is available, it prints a one-line update reminder. This check is silent and does not block the session if you are offline or if the check times out.

## Bind mARC to a repo (optional but recommended)

Run **`/marc:init`** in the consuming repo. It scaffolds the repository configuration (either `.claude/team.toml` for Claude Code or `.agents/team.toml` for Google Antigravity). This config pins the GitHub org and repo, the Project number, key source paths, and the validation command so `@techlead` and the specialists do not have to guess. It shows you every file before writing anything. If you prefer to set it up by hand, copy [`docs/team.toml.example`](docs/team.toml.example) and fill it in. Note that a repository's local `.claude/` or `.agents/` configuration folder overrides the plugin, which in turn overrides user config.

## Harness Architecture & Compatibility

To support multiple host tools, mARC uses a harness-based architecture. A single core team of agents is defined under `core/` and compiled into platform-specific plugins located in the `harnesses/` directory.

Currently, mARC supports two first-class harnesses: Claude Code (plugin under `harnesses/claude-code/marc/`) and Google Antigravity (plugin under `harnesses/antigravity/marc/`).

Because each host tool provides different agent execution APIs, the subagent dispatch mechanism varies between harnesses. Claude Code uses the native `Agent` tool to dispatch subagents, whereas Google Antigravity uses the `invoke_subagent` tool.

The leader skill `@techlead` dynamically inspects the available tools and maps its requests to the appropriate dispatch tool. For a detailed breakdown of tool schemas and configuration mappings, refer to [COMPATIBILITY.md](harnesses/antigravity/marc/COMPATIBILITY.md).

## Layout

```
core/                            # core specialist agent templates and leader skills
harnesses/
  claude-code/
    marc/                        # compiled Claude Code plugin
      .claude-plugin/plugin.json # plugin manifest
      skills/tech-lead/          # `@techlead` leader skill (/marc:tech-lead)
      skills/init/               # `/marc:init`, opt-in per-repo onboarding
      agents/                    # `@dev`, `@sre`, `@design`, `@sec`, `@research` shared specialist bench
      hooks/hooks.json           # SessionStart hook to inject `.claude/team.toml`
  antigravity/
    marc/                        # compiled Google Antigravity plugin
      plugin.json                # plugin manifest
      skills/                    # symlinked leader skills (e.g. `/marc:tech-lead`, `/marc:init`)
      agents/                    # symlinked specialist agents
      COMPATIBILITY.md           # compatibility tracker for Google Antigravity
docs/
  ARCHITECTURE.md                # growth model covering the different roles and harnesses
  marc/                          # durable team artifacts like decision records and research briefs
  team.toml.example
install.sh                       # installer for Claude Code
```

The plugin is structured so that the core logic under `core/` compiles into platform-specific configurations in the `harnesses/` directory. This allows the project to grow sideways into new harnesses or upward into new leader skills like `founder` and `eng-director` without restructuring the repository. Detailed growth plans are documented in [ARCHITECTURE.md](docs/ARCHITECTURE.md).

## License

MIT, see [LICENSE](LICENSE).

```
*** `@techlead` sets mode +v on your next idea
```
