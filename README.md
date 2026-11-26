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

**mARC (Multi-Agent Relay Control)** is a plugin that puts a full AI engineering team in your terminal. Install it once for Claude Code, OpenAI Codex, Google Antigravity or GitHub Copilot CLI, and reuse the same team in every repo. It is built by the team it ships, and the receipts are public.

## Install

Needs Node.js 20+ and at least one harness CLI on your PATH.

```bash
npx @nexaduo/marc install
```

That detects your harnesses and installs or updates mARC on each one. It runs their own plugin CLIs, shows every command first, and never writes config itself. Then open your harness and run `/marc:tech-lead`.

Options (details in [`launcher/README.md`](launcher/README.md)):

- `--harness <claude|copilot|codex|agy>`: act on one harness (repeatable)
- `--dry-run`: print the plan and change nothing
- `--yes`: confirm mutations without a prompt (required in scripts)
- `--scope <user|project|local>`: Claude scope only
- `--version <tag>`: must equal the launcher's embedded release
- `--force`: replace a nexaduo marketplace registered from another source or a dev clone

```bash
npx @nexaduo/marc install --force
```

`npx @nexaduo/marc doctor` checks your install (read-only). Everything else: [CLI reference](launcher/README.md), [install and update](docs/install.md).

## The team, in brief

Think of your project as an IRC channel. `@techlead` holds channel-operator status: it turns the discussion into tracked work on your GitHub board and pings the right specialist.

`@dev` builds, `@sre` keeps it running, `@design` does the front end, `@sec` and `@rev` are the merge gate, `@research` brings cited evidence, and `@arch` settles one hard call. Running several teams? `@eng-director` coordinates them.

Roster and how teams coordinate: [docs/teams.md](docs/teams.md).

## Learn more

- [Install, update, bind to a repo](docs/install.md)
- [Harnesses and Codex compatibility](docs/harnesses.md)
- [Coordinating several teams](docs/teams.md)
- [Architecture and repository layout](docs/ARCHITECTURE.md)
- [Releases](docs/releases.md)
- [Receipts](docs/marc/): the [board](https://github.com/orgs/NexaDuo/projects/2), [issues](https://github.com/NexaDuo/mARC/issues?q=is%3Aissue), [pull requests](https://github.com/NexaDuo/mARC/pulls?q=is%3Apr) and decision records behind this repo
- [marc.nexaduo.com](https://marc.nexaduo.com)

## License

MIT, see [LICENSE](LICENSE).

```
*** `@techlead` sets mode +v on your next idea
```
