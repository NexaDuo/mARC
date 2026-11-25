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
npx @nexaduo/marc update      # update the plugin on every detected harness
npx @nexaduo/marc doctor      # read-only: versions, drift, marketplace source
npx @nexaduo/marc uninstall   # remove the plugin (marketplaces are kept)
```

Then open any repository in your harness and run `/marc:tech-lead`.

| Flag | Effect |
|---|---|
| `--harness <claude\|copilot\|codex\|agy>` | Act only on the named harness; repeat for several. Default: every harness on `PATH` |
| `--scope <user\|project\|local>` | Claude Code only: the scope to install, update or uninstall |
| `--version <tag>` | Must equal the release embedded in this package; otherwise prints the matching `npx` command and exits |
| `--dry-run` | Print the plan and change nothing |
| `--yes` | Skip the confirmation prompt. Required for `install`, `update` and `uninstall` when not in a terminal; `doctor` and `--dry-run` never need it |

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

Each npm version embeds one mARC release tag. A tag `vYY.M.D[.N]` (no leading
zeros) maps to npm `YY.(M*100+D).N`, where a missing `.N` is `0`. For example,
`v26.11.25.3` is `26.1125.3` and `v26.12.1` is `26.1201.0`.

What a given package version pins:

- **Antigravity** installs exactly the embedded tag, verified by commit.
- **Claude Code, Copilot and Codex** install from the `nexaduo` marketplace,
  whose catalog points at the latest mARC release. An older package still gives
  these harnesses the latest release, and `doctor` reports any harness whose
  installed version differs from the embedded one.

Use the latest package to keep every harness on the same release.

Releases from `26.1125.3` on are published from GitHub Actions with npm
provenance. `npx` does not verify provenance for you. To check it, run
`npm audit signatures` in a project that depends on the package; pinning an
exact version then keeps you on the release you checked.

## Links

- Source, docs and issues: https://github.com/NexaDuo/mARC
- License: MIT
