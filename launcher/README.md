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
| `--dry-run` | Print the plan and change nothing (`install` still runs the read-only `plugin list` first) |
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
- `install` never upgrades an existing install. If the installed version differs
  from the launcher release it prints `STALE` and exits non-zero; run `update`.
- `doctor` changes nothing and exits non-zero when it finds drift. Claude
  `project` and `local` rows add the project they belong to as an extra field
  (`~/repos/app` under your home directory, otherwise the absolute path). A harness whose only
  problem is an older version, or a marketplace catalog still tracking `main`,
  is `STALE`: run `npx @nexaduo/marc update`. `FAILED` means something else is
  wrong (marketplace missing or drifted, plugin missing, release source
  unverifiable, a native CLI error) and wins over `STALE`.

## Versions

Each npm version embeds one mARC release tag. A tag `vYY.M.D[.N]` (no leading
zeros) maps to npm `YY.(M*100+D).N`, where a missing `.N` is `0`. For example,
`v26.11.25.5` is `26.1125.5` and `v26.12.1` is `26.1201.0`.

What a given package version pins:

- **Antigravity** installs exactly the embedded tag, verified by commit.
- **Claude Code, Copilot and Codex** install from the `nexaduo` marketplace,
  whose catalog points at the latest mARC release. An older package still gives
  these harnesses the latest release, and `doctor` reports any harness whose
  installed version differs from the embedded one.

Use the latest package to keep every harness on the same release.

Releases are published from GitHub Actions with npm provenance once the npm
trusted publisher is bound (#426); until then npm may lag the git tag. `npx` does not verify provenance for you. To check it, run
`npm audit signatures` in a project that depends on the package; pinning an
exact version then keeps you on the release you checked.

## Console colors

Output is colored only when stdout is a terminal. Piped, redirected and CI
output is plain text with no escape sequences, identical to earlier releases.

- `NO_COLOR=1` (any non-empty value) turns color off.
- `FORCE_COLOR=1|2|3` turns it on even when piped (`2` implies 256 colors, `3`
  truecolor); `FORCE_COLOR=0` turns it off. `NO_COLOR` wins over `FORCE_COLOR`.
- Truecolor is used when `COLORTERM` is `truecolor` or `24bit`, 256 colors when
  `TERM` contains `256color`, otherwise 16 colors. `TERM=dumb` and legacy
  Windows consoles stay plain.
- `COLORFGBG` ending in `7` or `15` selects a light-background palette.

## Links

- Source, docs and issues: https://github.com/NexaDuo/mARC
- License: MIT
