# mARC Architecture & Growth Model

mARC packages an agent *team* as a portable Claude Code plugin. This document
describes how the repo is meant to grow — along three independent axes — and why
the layout is shaped the way it is.

The full directory tree is under [Repository layout](#repository-layout).

The root `marketplace.json` and the plugin's own `plugin.json` live in **separate**
`.claude-plugin/` directories (root vs. `harnesses/claude-code/marc/`). The
marketplace points at a released plugin under `harnesses/claude-code/marc/`.
`npx @nexaduo/marc install` delegates marketplace registration and installation
to the native plugin CLIs; the launcher never writes configuration itself.
(Do **not** use `metadata.pluginRoot` — it is version-dependent.)

## Axis 1 — Leaders are *skills*

Each leadership persona is a **skill** under the plugin's `skills/` directory,
invoked as `/marc:<leader>`.

- **Today:** `tech-lead` (`/marc:tech-lead`) — ops the channel, turns a
  discussion into delegated work, and convenes the specialist bench.
- **Engineering director:** `eng-director` coordinates independent techlead
  sessions, operational recovery and delegated shared-resource supervision from
  an optionally private workspace. See `core/skills/eng-director/SKILL.md`.
- **Planned growth:** `founder`, `c-level` — each a new
  `skills/<leader>/SKILL.md`, invoked `/marc:<leader>`. One plugin can host many
  skills with no collision.

A leader's job is orchestration: understand intent, pick the right specialists,
delegate, and integrate the results. Adding a leader is purely additive — drop a
new skill directory, no other file needs to move.

## Axis 2 — Specialists are a shared flat `agents/` pool

The specialists are subagents in a single flat pool (`agents/` on most harnesses), shared by
**every** leader:

- `@dev` (`engineer.md`), `@sre` (`sre.md`), `@design` (`design.md`),
  `@sec` (`security.md`), `@rev` (`review.md`), `@research` (`research.md`), `@arch` (`architect.md`).

Any leader skill can convene any specialist — the bench is common infrastructure,
not owned by a particular leader. New specialists are added by dropping another
`agents/<name>.md`; existing leaders can immediately dispatch them.

The source of the pool is `core/agents/`. Most harnesses compile it to an
`agents/` directory. Antigravity is the exception: its `compile.json` sets
`agents_as_skills: true`, so each persona compiles to `skills/<name>/SKILL.md`
(native role resolution there did not load agent personas), and the dispatch text
enforces tool boundaries through `define_subagent` rather than persona frontmatter.
See `harnesses/antigravity/marc/COMPATIBILITY.md`.

## Axis 3 — Multi-harness are `harnesses/<harness>/` siblings

The plugin is nested under `harnesses/claude-code/marc/` so the repo can host the
**same team** rendered for other agent harnesses as sibling subtrees, each in that
harness's native format:

```
harnesses/
├── claude-code/marc/     # Claude Code plugin
├── codex/marc/           # OpenAI Codex plugin
├── antigravity/marc/     # Google Antigravity plugin
└── copilot/marc/         # GitHub Copilot CLI plugin
```

Nesting the Claude Code plugin one level deeper does not affect Claude Code:
routers resolve the plugin via the marketplace `source` path, and everything the
plugin references internally stays relative to its own root.

## Deliberately deferred (YAGNI)

A **harness-neutral source of truth + a render/build step** that generates each
harness's format from one definition is *not* built now. It is real work with no
payoff until a second harness exists, so it arrives **only when the 2nd harness
does**. To keep that future step cheap, the role prose in `skills/` and `agents/`
is written **harness-neutral** (describing behavior and responsibilities, not
Claude-Code-specific mechanics) — so the eventual extraction is mostly a move, not
a rewrite.

## Repository layout

```
.claude-plugin/marketplace.json  # marketplace "nexaduo"; lists the plugin(s)
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
README.md  LICENSE  CHANGELOG.md  VERSION
```

The plugin is structured so that the core logic under `core/` compiles into platform-specific configurations in the `harnesses/` directory. This allows the project to grow sideways into new harnesses or upward into further leader skills like `founder` without restructuring the repository. The growth plans are above.
