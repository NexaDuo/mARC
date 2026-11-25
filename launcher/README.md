# @nexaduo/marc

Installer for [mARC](https://github.com/NexaDuo/mARC) (Multi-Agent Relay
Control), a team of AI engineering agents (`@techlead`, `@dev`, `@sec`, `@rev`,
`@sre`, `@design`, `@research`) that you install once and use in any repository.

It installs **and keeps in lockstep** the mARC plugin for every supported
harness found on your `PATH`:

| Harness | CLI used |
|---|---|
| Claude Code | `claude plugin …` |
| GitHub Copilot CLI | `copilot plugin …` |
| OpenAI Codex | `codex plugin …` |
| Google Antigravity | `agy plugin …` |

## Usage

Requires Node.js 20 or later and at least one harness CLI on your `PATH`.

```bash
npx @nexaduo/marc install     # install on every detected harness
npx @nexaduo/marc update      # move every harness to this package's release
npx @nexaduo/marc doctor      # read-only: versions, drift, marketplace source
npx @nexaduo/marc uninstall   # remove the plugin (marketplaces are kept)
```

Then open any repository in your harness and run `/marc:tech-lead`.

| Flag | Effect |
|---|---|
| `--harness <claude\|copilot\|codex\|agy>` | Limit to one harness (repeatable) |
| `--scope <user\|project\|local>` | Claude Code install scope |
| `--dry-run` | Print the plan and change nothing |
| `--yes` | Skip the confirmation prompt (required when not in a terminal) |

## What it does, and what it doesn't

- It runs only each harness's own plugin commands. It never writes harness
  settings or config files itself.
- Before anything changes it prints a plan and asks for confirmation. A few
  steps (such as the Antigravity clone path or Claude update scopes) are
  resolved while running and printed when they run.
- Antigravity installs from a shallow clone of the release tag, checked against
  the commit embedded in this package, then deletes the clone.
- `uninstall` never removes a marketplace registration.
- `doctor` changes nothing and exits non-zero when it finds drift.

## Versions

Each npm version embeds exactly one mARC release:
`vYY.MM.DD[.N]` maps to `YY.(MM*100+DD).N`. For example, `v26.11.25.3` is
`26.1125.3`. Run a specific release with `npx @nexaduo/marc@<version> install`.

Releases from `26.1125.3` on are published from GitHub Actions with npm
provenance. `npx` does not verify provenance for you. To check it, run
`npm audit signatures` in a project that depends on the package, or pin an
exact version.

## Links

- Source, docs and issues: https://github.com/NexaDuo/mARC
- License: MIT
