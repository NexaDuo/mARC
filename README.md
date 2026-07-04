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

```
*** Now talking in #marc
*** Topic: turn a discussion into tracked, delegated, shipped work
--> @techlead has been given channel operator status
    @techlead: specialists, standby. i'll convene you when there's work.
    @dev @sre @design @sec: ready.
```

**mARC (Multi-Agent Relay Control)** packages a full software-delivery **agent
team** as a portable [Claude Code](https://claude.com/claude-code) **plugin +
self-marketplace**, dressed in a retro/vaporwave **IRC** console aesthetic. It is
**not** a new orchestration engine — Claude Code's Agent Teams already handle
dispatch, parallelism, and isolation. mARC is the *package and the brand*: one
generic team you install once, user-scope, and reuse across every repo.

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

`@techlead` is a **skill** (`/marc:tech-lead`); `@dev`, `@sre`, `@design`, `@sec`
are **subagents** it dispatches. `@techlead` is the first of several leader
skills — `founder`, `eng-director`, and `c-level` are the planned growth path,
each convening the same shared specialist bench.

## Generic by design — per-repo binding lives in the consuming repo

The team carries **no** hardcoded stack facts. When it runs in a repo, it learns
that repo at runtime:

- It reads the repo's `AGENTS.md` / `CLAUDE.md` for architecture, release phases,
  and lessons.
- It reads that repo's **`.claude/team.config`** for the concrete bindings — gh
  org/repo, project number, key paths, the validation command, release-phase
  facts. See [`docs/team.config.example`](docs/team.config.example).
- If `team.config` is absent, `@techlead` discovers the repo/project dynamically
  (`gh repo view`, `gh project list --owner <org>`) instead of guessing.

A `SessionStart` hook prints the active `team.config` into context at the top of
each session (and a friendly note if none exists).

## Install (user-scope → available in every repo)

```
/plugin marketplace add NexaDuo/mARC
/plugin install marc@nexaduo
```

Or run the auditable installer (adds the marketplace + installs the plugin, prints
the banner):

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

Drop a `.claude/team.config` into the consuming repo (copy
[`docs/team.config.example`](docs/team.config.example) and fill it in). This pins
the GitHub org/repo, the Project number, the key source paths, and the validation
command so `@techlead` and the specialists stop guessing. Precedence to remember:
a repo's own `.claude/` overrides the plugin, which overrides user config.

## Layout

```
.claude-plugin/
  marketplace.json               # marketplace "nexaduo" → lists the marc plugin
harnesses/
  claude-code/
    marc/                        # THE Claude Code plugin
      .claude-plugin/plugin.json # plugin manifest (name marc, v0.1.0)
      skills/tech-lead/          # @techlead leader skill (/marc:tech-lead)
      agents/                    # @dev, @sre, @design, @sec shared specialist bench
      hooks/hooks.json           # SessionStart → inject .claude/team.config
docs/
  ARCHITECTURE.md                # growth model: leaders, specialists, harnesses
  team.config.example
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
