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
npx @nexaduo/marc install     # install, or update a stale install, on every detected harness
npx @nexaduo/marc update      # deprecated alias of install
npx @nexaduo/marc doctor      # read-only: versions, drift, marketplace source
npx @nexaduo/marc uninstall   # remove the plugin (marketplaces are kept)
```

Then open any repository in your harness and run `/marc:tech-lead`.

| Flag | Effect |
|---|---|
| `--harness <claude\|copilot\|codex\|agy>` | Act only on the named harness; repeat for several. Default: every harness on `PATH` |
| `--scope <user\|project\|local>` | Claude Code only: the scope to install (and update) or uninstall; default `user` |
| `--version <tag>` | Must equal the release embedded in this package; otherwise prints the matching `npx` command and exits |
| `--dry-run` | Print the plan and change nothing (`install` still runs the read-only `plugin list` and `plugin marketplace list` first) |
| `--force` | `install` only: re-register a nexaduo marketplace registered from another source (see below). The plan names the registration it removes. A local or unverifiable source (a possible dev clone) also needs you to type `replace` in a terminal |
| `--all-scopes` | `install` only, Claude: also update every stale `project`/`local` install whose directory still exists (native `plugin update --scope <scope>` run in that project). Without it a terminal run offers a multi-select and `--yes` or a non-terminal run only prints hints. Missing directories are reported as skipped (removal of those registry entries is tracked in #470), never touched. Needs Claude in the selection; cannot combine with `--scope`. With no nexaduo marketplace registered it updates nothing and exits non-zero |
| `--yes` | Skip the plan confirmation. Required for `install` and `uninstall` when not in a terminal; `doctor` and `--dry-run` never need it. It never answers the typed `replace` prompt |

## What it does, and what it doesn't

- It runs only each harness's own plugin commands. It never writes harness
  settings or config files itself.
- Before anything changes it prints a plan and asks for confirmation. A few
  steps (such as the Antigravity clone path) are
  resolved while running and printed when they run.
- Antigravity installs from a shallow clone of the release tag, checked against
  the commit embedded in this package, then deletes the clone.
- `uninstall` never removes a marketplace registration.
- Without `--force`, a nexaduo marketplace registered from another source is
  refused with a manual remedy and left untouched. A Claude registration by git
  URL (`https://github.com/NexaDuo/mARC.git`) counts as the right source. A Codex
  entry that lists only `name` and `root` (seen on Windows) cannot be verified: it
  is reported as `registered from local root <root>` and the result says
  `not installed` when mARC is absent.
- `--force` re-registers it: the plan (also in `--dry-run`) shows the found source
  and the exact `marketplace remove nexaduo`, `marketplace add NexaDuo/mARC` and
  reinstall commands, and nothing runs before your confirmation (`--yes` works
  without a terminal). Only `nexaduo` and `marc@nexaduo` are touched; if more than
  one nexaduo registration is listed it refuses. A foreign or pinned source needs
  the plan's confirmation. A local-checkout or unverifiable source is only
  replaced after you type `replace` at a per-harness prompt in a terminal, asked
  after the plan's `y/N`; `--yes` or no terminal refuses it, and declining
  refuses that harness only (the run exits non-zero). There is no unattended
  override: automation that must replace a dev clone runs the printed manual
  commands itself. The registration is re-read after you confirm and nothing is
  removed if it changed. If `add` fails the old source is not restored, the later
  harnesses skip their remove, and the exact retry commands are printed.
- `install` converges. It installs when missing, updates a stale install of the
  requested scope (default `user`) and does nothing when current. All read-only
  checks run before the plan, so the exact `marketplace update` / `plugin update`
  commands are shown before you confirm; if a read fails, nothing changes. A
  stale Claude row at another scope is reported with the command to run from
  that directory; in a terminal a multi-select offers to update the stale
  `project`/`local` rows (entries whose directory is gone are marked `missing`,
  unchecked, and never updated), and `--all-scopes` updates every existing one.
  Each chosen project's `claude plugin update marc@nexaduo --scope <scope>`
  runs with that project as the working directory and is listed in the plan;
  no unselected project is written. `update` is a deprecated alias with identical
  behavior.
- `doctor` changes nothing and exits non-zero when it finds drift. Claude
  `project` and `local` rows add the project they belong to as an extra field
  (`~/repos/app` under your home directory, otherwise the absolute path). A harness whose only
  problem is an older version, or a marketplace catalog still tracking `main`,
  is `STALE`: run `npx @nexaduo/marc install` (`install --all-scopes` when a
  `project` or `local` row with an existing directory is the stale one; a row whose
  directory is gone is reported separately with a manual remedy, see #470). `FAILED` means something else is
  wrong (marketplace missing, fixed by `<harness> plugin marketplace add NexaDuo/mARC`, or drifted, plugin missing, release source
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
- A block-letter banner prints in color mode on terminals of 40+ columns; `NO_COLOR` or a narrow or non-UTF-8 terminal gets the one-line header.

## Links

- Source, docs and issues: https://github.com/NexaDuo/mARC
- License: MIT
