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
--> @techlead has been given channel operator status
    @techlead: specialists, standby. i'll convene you when there's work.
    @dev @sre @design @sec @research: ready.
```

**mARC (Multi-Agent Relay Control)** packages a full software-delivery **agent
team** as a portable [Claude Code](https://claude.com/claude-code) **plugin +
self-marketplace**, dressed in a retro/vaporwave **IRC** console aesthetic. It is
**not** a new orchestration engine — Claude Code's Agent Teams already handle
dispatch, parallelism, and isolation. mARC is the *package and the brand*: one
generic team you install once, user-scope, and reuse across every repo.

## Try it in two minutes

Inside Claude Code:

```
/plugin marketplace add NexaDuo/mARC
/plugin install marc@nexaduo
/marc:tech-lead
```

That's the whole ritual: add the marketplace, install the plugin, summon the
channel operator. Install details, updates, and per-repo binding are covered
below.

## The metaphor — one channel, one op, a bench of specialists

Think of your project as an IRC channel. **`@techlead`** holds channel-operator
status: it listens to the discussion, compiles it into well-specified, tracked
work on your GitHub Project board, then pings the right specialist to do it.

| handle       | role                 | pings for                                            |
|--------------|----------------------|------------------------------------------------------|
| `@techlead`  | channel operator     | convene, spec, record on the board, dispatch, track  |
| `@dev`       | engineer             | app/service code, IaC, deploy scripts, schema, tests |
| `@sre`       | reliability          | deploys, observability, incidents, backups/DR, cost  |
| `@design`    | front-end            | UI screens, UX, end-to-end web flows                 |
| `@sec`       | security (read-only) | pre-merge diff review — the mandatory merge gate     |
| `@research`  | researcher (read-only) | external evidence — benchmarks, papers, docs — as a cited brief |

`@techlead` is a **skill** (`/marc:tech-lead`); `@dev`, `@sre`, `@design`, `@sec`,
`@research` are **subagents** it dispatches. `@techlead` is the first of several leader
skills — `founder`, `eng-director`, and `c-level` are the planned growth path,
each convening the same shared specialist bench.

## Generic by design — per-repo binding lives in the consuming repo

The team carries **no** hardcoded stack facts. When it runs in a repo, it learns
that repo at runtime:

- It reads the repo's `AGENTS.md` / `CLAUDE.md` for architecture, release phases,
  and lessons.
- It reads that repo's **`.claude/team.toml`** for the concrete bindings — gh
  org/repo, project number, key paths, the validation command, release-phase
  facts. See [`docs/team.toml.example`](docs/team.toml.example).
- If `team.toml` is absent, `@techlead` discovers the repo/project dynamically
  (`gh repo view`, `gh project list --owner <org>`) instead of guessing.

A `SessionStart` hook prints the active `team.toml` into context at the top of
each session (and a friendly note if none exists — or a one-line deprecation
notice if only a pre-0.11.0 `team.config` is found; re-run `/marc:init` to
migrate).

## Receipts: this repo runs on mARC

mARC is built by the team it ships. Every spec, dispatch, security review, and
decision that produced this repo is public, so you can audit the process before
trusting it:

- the [Project board](https://github.com/orgs/NexaDuo/projects/2), where the
  tech-lead records and tracks every task,
- the [issues](https://github.com/NexaDuo/mARC/issues?q=is%3Aissue) and
  [pull requests](https://github.com/NexaDuo/mARC/pulls?q=is%3Apr), with the
  specs, findings comments, and pre-merge security reviews inline,
- [`docs/marc/`](docs/marc/), the durable decision records and research briefs.

## Install (user-scope → available in every repo)

The quick-start block above is the whole install. Alternatively, run the
auditable installer (adds the marketplace + installs the plugin, prints the
banner):

```
./install.sh
```

After install, `@techlead` is available as `/marc:tech-lead` in any repo, and it
dispatches the specialist subagents on demand.

## Update

**Recommended: enable auto-update for the `nexaduo` marketplace.** With auto-update
on, Claude Code pulls new plugin versions for you and you never drift behind — this
is the primary, drift-free path. Manage it from `/plugin` → the `nexaduo`
marketplace → enable auto-update.

To update manually at any time:

```
claude plugin update marc@nexaduo
```

or, from within Claude Code:

```
/plugin marketplace update nexaduo
/reload-plugins
```

**Safety net (for auto-update-off users):** mARC ships a `SessionStart` hook that,
once per session, checks whether your installed version is behind the version on
`main` and, if so, prints a single one-line nudge with the update command. It is
warn-only — it makes a short, timeout-bounded network check and degrades to a silent
no-op when offline, rate-limited, or tooling is missing; it never blocks or slows a
session. It only nudges on a minor/major difference, so routine patch releases won't
pester you.

## Bind mARC to a repo (optional but recommended)

Run **`/marc:init`** in the consuming repo. It scaffolds a `.claude/team.toml`
pinning the GitHub org/repo, the Project number, the key source paths, and the
validation command so `@techlead` and the specialists stop guessing, and it
shows you every file before writing anything. Prefer doing it by hand? Copy
[`docs/team.toml.example`](docs/team.toml.example) and fill it in; the result
is the same. Precedence to remember:
a repo's own `.claude/` overrides the plugin, which overrides user config.

## Layout

```
.claude-plugin/
  marketplace.json               # marketplace "nexaduo" → lists the marc plugin
harnesses/
  claude-code/
    marc/                        # THE Claude Code plugin
      .claude-plugin/plugin.json # plugin manifest (name marc; version tracks the badge above)
      skills/tech-lead/          # @techlead leader skill (/marc:tech-lead)
      skills/init/               # /marc:init, opt-in per-repo onboarding
      agents/                    # @dev, @sre, @design, @sec, @research shared specialist bench
      hooks/hooks.json           # SessionStart → inject .claude/team.toml
docs/
  ARCHITECTURE.md                # growth model: leaders, specialists, harnesses
  marc/                          # durable team artifacts: decision records, research briefs
  posts/                         # launch post drafts + published links
  team.toml.example
install.sh                       # safe, auditable installer + banner
```

The plugin is deliberately nested under `harnesses/claude-code/marc/` so the repo
can grow **sideways** into other harnesses (`harnesses/cursor/…`,
`harnesses/codex/…`) and **upward** into more leader skills (`founder`,
`eng-director`, `c-level`) without reshuffling. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) for the full growth model.

## License

MIT — see [LICENSE](LICENSE).

```
*** @techlead sets mode +v on your next idea
```
