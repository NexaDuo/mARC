# Decision record: mARC does not adopt `akitaonrails/ai-memory`

- **Type:** decision record
- **Date:** 2026-07-29
- **Attribution:** operator + user decision on
  [issue #175](https://github.com/NexaDuo/mARC/issues/175), based on the
  `@research` brief there
  ([#175 comment](https://github.com/NexaDuo/mARC/issues/175#issuecomment-5113662913)).
- **Status:** accepted (docs-only; no plugin version bump)

## Decision

mARC does **not** adopt `akitaonrails/ai-memory` — or any equivalent external
memory daemon — as a dependency. It keeps its current model: harness-native
auto-memory for session-local state, durable artifacts landed in `docs/marc/`
via reviewed PRs, and the GitHub Project board as the source of truth for work
state.

## What ai-memory actually is

Per the brief, verified against
[the project's README](https://github.com/akitaonrails/ai-memory) and
`gh repo view` (fetched 2026-07-29): a standalone Rust daemon exposing an
MCP/HTTP server on `127.0.0.1:49374`, with plain markdown as the source of
truth and a `db/` directory holding SQLite (FTS5 + optional embeddings) purely
for on-demand retrieval — not an always-loaded context. It supports a
zero-LLM / no-embeddings mode, writes via automatic lifecycle hooks, an MCP
tool, or a CLI (`ai-memory write-page`, with `--pinned` to exempt a page from
decay), is MIT-licensed, has 1,297 stars, and its latest release tag is
`v1.19.2`, with the repository last pushed 2026-07-28. It advertises
portability across agent CLIs including
Claude Code, Codex, Devin CLI, Cursor, Gemini CLI, Antigravity CLI, and VS Code
Copilot. This is an actively maintained, competent project, and the rejection
below is **not** a quality judgment.

## Why rejected

1. **It requires an out-of-repo install channel.** Distribution is Docker
   (`akitaonrails/ai-memory:latest`), an Arch AUR package, or native release
   binaries — there is no marketplace path. This directly contradicts the
   distribution decision recorded five days earlier in
   [`docs/marc/2026-07-24-decision-distribution-channel-marketplace-only.md`](2026-07-24-decision-distribution-channel-marketplace-only.md)
   (#161), which rejected even a lighter-weight `npx` installer for the same
   reason. Adopting a standalone Rust daemon as a core mARC capability would
   reopen that decision as a side effect of a memory choice — the wrong way to
   reopen it. If the marketplace-only decision itself is ever revisited on its
   own merits, this constraint should be re-checked then, not smuggled in
   here.
2. **Its autonomous hook-driven writes bypass mARC's only auditability gate.**
   A durable mARC memory today lands in `docs/marc/` through a PR reviewed by
   `@sec` and `@rev`. The brief checked three alternatives — Claude Code's
   native memory, Serena's project memories, and the official MCP
   knowledge-graph server (`@modelcontextprotocol/server-memory`) — and none
   of the four systems compared (ai-memory included) has a review gate before
   persistence; ai-memory's and Serena's writes, and the MCP server's
   `create_entities`/`add_observations`, all happen autonomously mid-session.
   For a plugin whose memories can become governance rules, an unreviewed
   write path is the wrong trade.
3. **A local daemon is new surface for a benefit already covered.** A
   persistent process with an open HTTP port and storage outside the repo
   adds an attack surface and an operational dependency for capability mARC's
   existing three-tier model (always-loaded auto-memory, PR-gated
   `docs/marc/`, the board) already provides.

## Counterpoint

ai-memory's strongest argument for mARC is genuine: cross-CLI shared memory
maps directly onto mARC's three-harness (Claude Code, Antigravity, Copilot)
problem. But that is precisely the part mARC cannot distribute through the
marketplace — adopting it would mean recommending users perform an install
mARC just decided not to ship.

## What we do take from it

Three conventions are worth borrowing into the existing model, without the
daemon, the install channel, or the unreviewed writes — tracked separately in
[issue #176](https://github.com/NexaDuo/mARC/issues/176):

- **Size-capped writes** — ai-memory caps captured observations (16 KiB for
  prompts/summaries, 2 KB for tool excerpts); oversized content should become
  a `docs/marc/` artifact, not a memory entry.
- **Pinned vs. decay** — a `--pinned` flag exempts a permanent invariant from
  decay, distinct from a time-bound entry that should carry an absolute
  expiry date.
- **On-demand recall index rather than always-loaded context** — ai-memory's
  hybrid FTS5/vector retrieval only surfaces a bounded handoff at session
  boundaries, unlike Claude Code's own `MEMORY.md`, which is unconditionally
  loaded (up to 200 lines / 25 KB) every session regardless of relevance.

## Side observation

Serena is available as an MCP server in some local dev environments (it is
not tracked in this repo — `.serena/` is gitignored, and `git ls-files` shows
no Serena paths). Where it is running, its project-memories feature — plain
markdown files in `.serena/memories/`, agent-fetched on demand by inferred
relevance, no daemon — offers exactly the on-demand, daemon-free pattern
above. If daemon-free on-demand project memory is ever wanted repo-wide,
that pattern is already reachable without adopting a new dependency, rather
than one that would need to be built.

## Revisit conditions

- The 2026-07-24 marketplace-only decision (#161) is itself revisited on its
  own merits, independent of this one.
- `ai-memory` (or an equivalent) becomes installable through the marketplace
  channel with a write path that can be routed through a reviewable gate
  before persistence.

## Consequences

- No version bump; this is a docs-only decision record with no code, IaC, or
  manifest change.
- The three borrowed conventions (size caps, pinned/decay, on-demand recall)
  are tracked as a separate follow-up in #176 and are not implemented by this
  record.

## Sources

- [`#175` `@research` brief](https://github.com/NexaDuo/mARC/issues/175#issuecomment-5113662913)
- [akitaonrails/ai-memory README](https://github.com/akitaonrails/ai-memory)
- [Claude Code Docs: Memory](https://code.claude.com/docs/en/memory)
- [Serena docs: Memories & Onboarding](https://oraios.github.io/serena/02-usage/045_memories.html)
- [modelcontextprotocol/servers memory README](https://github.com/modelcontextprotocol/servers/blob/main/src/memory/README.md)
- [modelcontextprotocol/servers issue #2689](https://github.com/modelcontextprotocol/servers/issues/2689)
- [`docs/marc/2026-07-24-decision-distribution-channel-marketplace-only.md`](2026-07-24-decision-distribution-channel-marketplace-only.md)
