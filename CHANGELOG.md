# Changelog

All notable changes to mARC are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-07-03

Initial release — the agent team extracted from a single repo into a portable,
cross-repo Claude Code plugin + self-marketplace, wrapped in an IRC/vaporwave
brand layer.

### Added
- `.claude-plugin/plugin.json` — plugin manifest (`marc`, v0.1.0, MIT).
- `.claude-plugin/marketplace.json` — self-marketplace entry pointing at the
  GitHub repo `NexaDuo/mARC` (the repo doubles as its own marketplace).
- `skills/tech-lead/SKILL.md` — `@techlead` channel-operator skill (`/tech-lead`)
  with **runtime discovery** of the target repo and Project board (via
  `.claude/team.config`, then `gh repo view` / `gh project list`) instead of
  hardcoded repo/project values.
- `agents/{engineer,sre,design,security}.md` — the `@dev`, `@sre`, `@design`,
  `@sec` specialist subagents, fully genericized (no stack-specific facts) and
  taught to read the consuming repo's `AGENTS.md` + `.claude/team.config` at
  runtime.
- IRC `@handle` identities across the roster (`@techlead`/`@dev`/`@sre`/`@design`/
  `@sec`) and a vaporwave ASCII-art console brand in the README, banner, and
  installer.
- `hooks/hooks.json` — a `SessionStart` hook that injects
  `$CLAUDE_PROJECT_DIR/.claude/team.config` into context (warns, never fails, if
  absent).
- `install.sh` — a safe, auditable installer (adds the marketplace + installs the
  plugin, prints the banner; no `curl | sh` of remote code).
- `README.md`, `LICENSE` (MIT), `.gitignore`, `docs/team.config.example`.

[0.1.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.1.0
