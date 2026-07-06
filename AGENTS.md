# AGENTS.md

mARC (**Multi-Agent Relay Control**) is a Claude Code **plugin + self-marketplace**
that packages a portable, cross-repo AI engineering team: `@techlead` (channel
operator) convenes specialists `@dev`, `@sre`, `@design`, `@sec`, `@research`. The
team's
*governance* travels via one plugin; each consuming repo keeps its own facts.

**This repo is special: it is BOTH the product's source AND where the team dogfoods
on itself.** Self-improvement lessons legitimately flush to source *here* — the one
context where that is allowed (see Constraints).

## Architecture
- **Plugin** lives at `harnesses/claude-code/marc/`; the **marketplace** manifest is
  at repo root `.claude-plugin/marketplace.json` (name `nexaduo`, plugin `marc` →
  install `marc@nexaduo`, invoke `/marc:tech-lead`).
- **Leaders = skills** (`skills/<leader>/`, invoked `/marc:<leader>`); **specialists
  = a shared flat pool** (`agents/*.md`). Any leader convenes any specialist.
- Nesting under `harnesses/<harness>/` reserves the namespace for future non–Claude-Code
  harnesses. The growth model is in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) —
  don't duplicate it here.

## Operating principles
These distill a comparative study of agent-orchestration frameworks (OpenHands,
MetaGPT, CrewAI, Claude Code Agent Teams) down to what holds for mARC:
- **Stateless dispatch / context hygiene.** Specialists run fresh per task; durable
  state lives in git, the board/issues, and this file — not in long conversations.
- **Spec-driven leverage.** A vague task propagates destructively across parallel
  agents. The tech-lead's main leverage is spec clarity + acceptance criteria *before*
  dispatch (the sufficiency gate).
- **Dispatch in the background; isolate parallel writers.** Synchronous dispatch blocks
  the operator channel — fan out in background and track via notifications. When
  specialists edit files concurrently, isolate them (git worktrees) to avoid clobbering.
- **Bounded loops.** Cap runaway debugging; stop and surface rather than burn tokens in
  an infinite fix loop.
- **No self-merge; independent review.** Every PR gets a security pass; the author can't
  self-approve.

## Constraints
- **Anti-anchoring / genericization (hard gate):** everything under `harnesses/` must
  stay **stack-agnostic** — zero references to any consuming repo's stack. CI enforces
  this with a grep gate. Repo-specific facts belong in the *consuming* repo's
  `AGENTS.md`/`.claude/team.config`, read at runtime — never hardcoded here.
- **Keep this file minimal.** Record only what isn't discoverable by reading the repo.
  If agents repeat a mistake, tighten the linter/CI/test — don't grow prose here. LLMs
  anchor on whatever sits in context, including deprecated caveats.
- **An installed plugin is immutable from the user's side.** Product changes flow only
  through releases: bump `harnesses/claude-code/marc/.claude-plugin/plugin.json`
  `version` + `CHANGELOG.md`; users get it via `claude plugin update marc@nexaduo`.
  Never edit the plugin cache; auto-update is OFF for third-party marketplaces.
- **Self-improvement is context-gated:** flush-to-source is allowed ONLY in this repo.
  When mARC runs *installed* in a user's repo, lessons persist **locally** (their
  `AGENTS.md`/`team.config`/memory); product-level lessons become **opt-in,
  human-approved, sanitized** upstream PRs — never autonomous, never leaking user
  context. This machinery lives in a dedicated `@scribe` agent, not the tech-lead skill.
- **No silent file writes** in any repo. Onboarding (`/marc:init`) is opt-in and shows
  content before writing.
- **Zero-config is a feature:** the team must work in any repo with no `team.config`
  (runtime discovery via `gh` + session memory). Don't regress that.

## Release phases
There is **no staging/prod deploy pipeline** — mARC is a distributable plugin, not a
hosted stack. Don't fake staging/prod phases. "Done" here means:
1. PR with **green CI** (`.github/workflows/ci.yml`: Tier 1 structural + Tier 2
   install/cross-repo — deterministic, no secret, no token cost).
2. **Security review before merge** (`@sec`); **no self-merge**. Skill/agent changes
   carry a high review bar (injection surface).
3. **Version bumped + CHANGELOG** updated.
4. For user-facing behavior, **dogfood in a real repo** and confirm the *terminal* state
   (PR/CI/logs), not the "enqueued" step.
- Validate locally with `claude plugin validate harnesses/claude-code/marc` (a benign
  `minimumVersion` "unknown field" warning is expected — don't "fix" it away).
- The landing page (`docs/` → GitHub Pages → marc.nexaduo.com) is served DNS-only; a
  `SessionStart` hook injects `team.config` and nudges on outdated versions.

## Lessons
- **Base freshness — branch from freshly-fetched `origin/main`.** Local `main` does NOT
  advance when PRs merge on the remote; a stale base makes already-merged work reappear
  as conflicts. For a stale open PR, run `gh pr update-branch`. (This bites humans too:
  always `git fetch` before reasoning about what's merged.)
- **Never delete flagged code on a stale base.** A security review on a stale base once
  nearly recommended *reverting live merged code*. Re-sync the base (the three-dot PR
  diff is authoritative), don't strip the flagged lines.
- **The self-improvement loop can bug itself.** Dogfooding surfaced that the improvement
  machinery misbehaves installed-vs-in-source — hence the context gating. Trust the
  gate, not the instinct to "just flush to source."
- **Don't fabricate work.** On a quiet channel, ask for the demand or triage the board;
  never invent tasks to look busy.
