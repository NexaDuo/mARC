# mARC Architecture & Growth Model

mARC packages an agent *team* as a portable Claude Code plugin. This document
describes how the repo is meant to grow — along three independent axes — and why
the layout is shaped the way it is.

```
mARC/                                    # repo root
├── .claude-plugin/
│   └── marketplace.json                 # marketplace "nexaduo"; lists the plugin(s)
├── harnesses/
│   └── claude-code/
│       └── marc/                        # THE Claude Code plugin
│           ├── .claude-plugin/plugin.json
│           ├── skills/<leader>/SKILL.md  # leaders (tech-lead today)
│           ├── agents/<specialist>.md    # shared flat specialist bench
│           └── hooks/hooks.json
├── docs/
│   ├── ARCHITECTURE.md                  # this file
│   └── team.config.example
└── install.sh  README.md  LICENSE  CHANGELOG.md  .gitignore
```

The root `marketplace.json` and the plugin's own `plugin.json` live in **separate**
`.claude-plugin/` directories (root vs. `harnesses/claude-code/marc/`). The
marketplace points at the plugin with a full relative `source`
(`"./harnesses/claude-code/marc"`), so `/plugin marketplace add NexaDuo/mARC`
followed by `/plugin install marc@nexaduo` resolves the nested plugin unchanged.
(Do **not** use `metadata.pluginRoot` — it is version-dependent; the full `source`
path is the portable mechanism.)

## Axis 1 — Leaders are *skills*

Each leadership persona is a **skill** under the plugin's `skills/` directory,
invoked as `/marc:<leader>`.

- **Today:** `tech-lead` (`/marc:tech-lead`) — ops the channel, turns a
  discussion into delegated work, and convenes the specialist bench.
- **Planned growth:** `founder`, `eng-director`, `c-level` — each a new
  `skills/<leader>/SKILL.md`, invoked `/marc:<leader>`. One plugin can host many
  skills with no collision.

A leader's job is orchestration: understand intent, pick the right specialists,
delegate, and integrate the results. Adding a leader is purely additive — drop a
new skill directory, no other file needs to move.

## Axis 2 — Specialists are a shared flat `agents/` pool

The specialists are subagents in a single flat `agents/` directory, shared by
**every** leader:

- `@dev` (`engineer.md`), `@sre` (`sre.md`), `@design` (`design.md`),
  `@sec` (`security.md`), `@research` (`research.md`).

Any leader skill can convene any specialist — the bench is common infrastructure,
not owned by a particular leader. New specialists are added by dropping another
`agents/<name>.md`; existing leaders can immediately dispatch them.

## Axis 3 — Multi-harness are `harnesses/<harness>/` siblings

The plugin is nested under `harnesses/claude-code/marc/` so the repo can host the
**same team** rendered for other agent harnesses as sibling subtrees, each in that
harness's native format:

```
harnesses/
├── claude-code/marc/     # Claude Code plugin (today)
├── cursor/…              # future — Cursor-native format
├── codex/…               # future
└── antigravity/…         # future
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
