# Decision record: the Claude Code plugin marketplace remains mARC's only supported install channel

- **Type:** decision record
- **Date:** 2026-07-24
- **Attribution:** operator + user decision on
  [issue #161](https://github.com/NexaDuo/mARC/issues/161), based on two
  `@research` briefs:
  [#159](https://github.com/NexaDuo/mARC/issues/159#issuecomment-5073088708)
  (`skills.sh` / `npx skills`) and
  [#160](https://github.com/NexaDuo/mARC/issues/160#issuecomment-5073523899)
  (third-party plugin registries, a self-owned `npx` installer, MCP
  distribution, and cross-harness channels).
- **Status:** accepted (docs-only; no plugin version bump)

## Context

mARC ships four load-bearing component classes as a Claude Code plugin: two
multi-file skills, six subagents (`agents/*.md`), plugin-level hooks
(`hooks/hooks.json`), and bundled scripts resolved at runtime via
`${CLAUDE_PLUGIN_ROOT}`. The Claude Code plugin marketplace install
(`claude plugin marketplace add NexaDuo/mARC && claude plugin install
marc@marc`, or the git-clone equivalent documented in the repo's own
`README.md`) is the only channel mARC has ever supported. Two issues asked
whether a lighter-weight, `npx`-style, clone-free channel — matching the
single-command ergonomics some competing ecosystems advertise — was
available or worth building.

## Options considered

- **`skills.sh` / `npx skills` (`vercel-labs/skills`)** — rejected. Per #159,
  read directly from the CLI's source (`src/installer.ts`, `src/skills.ts`,
  `src/agents.ts`, `src/types.ts`, fetched 2026-07-24): the tool's data model
  only understands directories containing a `SKILL.md`. It has no code path
  for subagents or plugin-level hooks, and a plain skills.sh install leaves
  `${CLAUDE_PLUGIN_ROOT}` unset (the env var is only set when Claude Code
  loads a directory as a plugin). Installed this way, mARC's `tech-lead`
  skill would load, look legitimate, and then fail the moment it tried to
  dispatch a subagent (`subagent_type: marc:engineer`) or resolve a
  plugin-relative script path — a worse outcome than no install at all. The
  `marc:` namespace also does not survive: a bare skills.sh install gets no
  namespace prefix, and the "nest a `.claude-plugin/plugin.json` inside a
  skill folder" workaround (unverified, undocumented anywhere as an actual
  skills.sh workflow) would still resolve to `<name>@skills-dir`, not
  `marc:`.
- **Third-party plugin manager (`npx claude-plugins`, `Kamalnrf/claude-plugins`,
  `claude-plugins.dev`)** — rejected. Per #160, read directly from the CLI's
  source (`packages/cli/src/commands/install.ts`, `src/core/resolver.ts`,
  `src/utils/fs.ts`, `src/utils/git.ts`, `src/utils/validation.ts`, fetched
  2026-07-24): this tool writes to the same files Claude Code itself reads
  (`~/.claude/settings.json`'s `enabledPlugins`, `known_marketplaces.json`),
  and would in principle carry full fidelity since it clones the whole repo
  rather than cherry-picking components. It fails two preconditions for mARC
  today: it requires `.claude-plugin/` at the git repo root (mARC's manifest
  is nested at `harnesses/claude-code/marc/.claude-plugin/plugin.json`), and
  name resolution only accepts identifiers pre-indexed in the tool's own
  third-party-hosted registry (`api.claude-plugins.dev`, reported to run on
  Val Town, a hobbyist-tier host) — raw URLs are explicitly rejected. Using
  it would require restructuring mARC into a plugin-manifest-at-repo-root
  layout and getting listed in a registry with no visible vetting pipeline,
  for a channel with no capability gain over the native marketplace.
- **A self-owned `npx` installer (option c)** — rejected. Buildable (roughly
  350-450 lines of Node/TypeScript by comparison with `claude-plugins`'
  equivalent modules, per #160), and it would carry all four component
  classes since it's mARC's own code. But it reinvents what
  `claude plugin marketplace add` / `claude plugin install` already do, and
  introduces the exact failure mode the motivating issue flagged: a
  project-owned script mutating `~/.claude/settings.json` outside Claude
  Code's own validated install path, for a team that is, per this repo's own
  `AGENTS.md`, already entirely inside the Claude Code plugin ecosystem.
- **MCP registry distribution** — out of scope by design, not by gap. Per
  #160 (`modelcontextprotocol.io/registry/about`, fetched 2026-07-24), the MCP
  registry hosts metadata for MCP servers only and is "not intended to be
  directly consumed by host applications" — it has no model for skills,
  subagents, or hooks at all.
- **Cross-harness channel (a single install path spanning Antigravity,
  Copilot, Cursor)** — not viable today. Per #160 and the prior #151/#152
  finding (`docs/marc/2026-07-21-brief-agy-marketplace-clone-free-install.md`),
  no registry indexes all four component classes across harnesses; Cursor has
  no subagent or hooks concept comparable to Claude Code's, and Antigravity's
  `agy plugin install` has no discovered clone-free/marketplace path for
  third-party repos.
- **Native Claude Code plugin marketplace (current channel)** — kept. It is
  the one data model in this survey natively defined to carry all four
  component classes together, already supports a non-interactive, scriptable,
  two-command install (`claude plugin marketplace add` + `claude plugin
  install`) with no `npx` and no user-side clone, and additionally offers a
  zero-command declarative path via `extraKnownMarketplaces` /
  `enabledPlugins` in `.claude/settings.json` for team onboarding. It also has
  a reserved-namespace anti-impersonation check with no equivalent in any
  third-party channel surveyed.

## Decision

**The Claude Code plugin marketplace remains mARC's only supported install
channel.** `skills.sh` / `npx skills`, the third-party `npx claude-plugins`
registry, and a self-owned `npx` installer are each considered and rejected.
This is a closed decision: do not re-evaluate these three options absent a
material change in the underlying facts (see "Revisit conditions" below).

## Constraints carried into implementation

- No plugin manifest, harness code, or `README.md` install/update
  instructions change as a result of this decision — the existing
  marketplace install path was already correct and is left as-is.
- Any future skill or subagent authoring must not assume a lighter-weight
  install channel exists; dispatch text (e.g. `subagent_type: marc:engineer`)
  and `${CLAUDE_PLUGIN_ROOT}`-relative script paths remain valid only under a
  full marketplace/plugin install.

## Consequences

- No version bump; this is a docs-only decision record with no code, IaC, or
  manifest change.
- Future proposals to add `skills.sh`, `npx claude-plugins`, or a self-owned
  `npx` installer as an *additional* channel should be checked against this
  record first rather than re-run as fresh research, unless one of the
  revisit conditions below has occurred.

## Revisit conditions

- **`skills.sh`:** if `vercel-labs/skills` adds a data model for subagents
  and plugin-level hooks, and a way to preserve the `marc:` namespace.
- **`npx claude-plugins`:** if mARC's plugin manifest ever moves to a
  standalone repo root (rather than nested under `harnesses/claude-code/marc/`)
  *and* `claude-plugins.dev`'s registry gains a visible review/vetting
  pipeline comparable to a package-registry security scan.
- **Self-owned `npx` installer:** if the native marketplace's two-command
  install path is ever removed or significantly degraded in ergonomics, such
  that building a bespoke installer becomes the only remaining option.
