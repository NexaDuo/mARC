# Changelog

All notable changes to mARC are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.4.0] - 2026-07-03

The tech-lead operator now dispatches specialists **in the background** by default,
so the main conversation never freezes waiting on a slow subagent — the channel
stays responsive and multiple items run concurrently.

### Changed
- `skills/tech-lead/SKILL.md` — Dispatch (step 4) rewritten to instruct background
  dispatch by default (`run_in_background: true` on every Agent call); the operator
  is notified on completion and can resume/continue a running agent by id. Clarifies
  that "don't wait for confirmation" (don't pause for the user's "go") is **not**
  "block on the subagent" (sit synchronously until it returns). Independent items
  still fan out in parallel; **dependent** work stays sequenced but via background +
  the notification/track loop rather than synchronous blocking. `run_in_background:
  false` is reserved for a genuine strict dependency whose result is needed before
  anything else in the same turn — and even then background is preferred. Track-to-
  done (step 5) updated to note the operator stays responsive and is re-invoked when
  each background agent finishes.
- `.claude-plugin/plugin.json` — version `0.3.0` → `0.4.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

## [0.3.0] - 2026-07-03

Team-operation rules flushed from session learnings into the versioned plugin —
two generic, repo-agnostic guardrails for sequenced PRs and stale-base security
reviews.

### Changed
- `skills/tech-lead/SKILL.md` — two rules added. **Dispatch (step 4):** when
  dispatching PRs in sequence, instruct each specialist to branch from
  freshly-fetched `origin/main` (`git fetch origin && git checkout -b <branch>
  origin/main`), because merging a prior PR via `gh pr merge` does not advance the
  local `main`; a stale PR is re-synced with `gh pr update-branch <N>`, not
  re-cut. **Principles (verification):** before acting on a security reviewer's
  "MERGE BLOCKED", confirm the finding against GitHub's authoritative three-dot PR
  diff — a stale local base can misattribute a prior merged PR's changes to the PR
  under review, and the fix is `gh pr update-branch`, never deleting the flagged
  (already-merged) code.
- `agents/security.md` — before reviewing, `git fetch origin` and confirm the
  branch base is fresh (`git merge-base --is-ancestor origin/main HEAD`), then
  review via the three-dot PR diff so a prior merged PR's changes on a stale base
  aren't misattributed to the PR under review.
- `.claude-plugin/plugin.json` — version `0.2.0` → `0.3.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

[0.4.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.4.0
[0.3.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.3.0

## [0.2.0] - 2026-07-03

Opt-in onboarding — a repo can now graduate from ephemeral session-memory to a
persistent, versioned team binding, without ever writing a file silently and
without changing the zero-config default.

### Added
- `skills/init/SKILL.md` — the `/marc:init` onboarding skill. Discovers the
  repo's org/repo/project **at runtime via `gh`** and prefills three
  **independently opt-in** artifacts, each shown verbatim and written only on an
  explicit "yes": `.claude/team.config` (prefilled from the
  `docs/team.config.example` schema, unknowns left as clearly-marked `TODO`
  placeholders), an optional lean `AGENTS.md` **skeleton of section headings
  only** (no placebo prose, per the anti-anchoring lesson), and an optional
  `enabledPlugins` pin **merged** into `.claude/settings.json` (the deliberate
  "adopt for good" step — merge, never clobber). Nothing is ever written
  silently.

### Changed
- `skills/tech-lead/SKILL.md` — first-run offer: when **both** `AGENTS.md` and
  `.claude/team.config` are absent, `@techlead` offers to run `/marc:init`
  (explaining that session memory is ephemeral while `team.config` stabilizes
  board/paths across sessions) and proceeds only on confirmation. Zero-config
  behavior is byte-for-byte unchanged if declined.
- `.claude-plugin/plugin.json` — version `0.1.0` → `0.2.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

[0.2.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.2.0

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
- Forward-compatible, multi-harness-ready layout: the Claude Code plugin is
  nested under `harnesses/claude-code/marc/` (manifest, `skills/`, `agents/`,
  `hooks/`), while the root `.claude-plugin/marketplace.json` (marketplace
  `nexaduo`) lists it via a full `source` path. Leaders live as `skills/`,
  specialists as a shared flat `agents/` pool, and future harnesses get their
  own `harnesses/<harness>/` sibling. Documented in `docs/ARCHITECTURE.md`.

[0.1.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.1.0
