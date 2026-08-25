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
- **`core/` is the editable source; `harnesses/*/marc/` (skills, agents) is compiled
  output — never hand-edit it.** Run `scripts/compile_prompts.py` to regenerate from
  `core/` + each harness's `compile.json`, and commit the regenerated files.

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
- **Tiered, size-capped memory with absolute decay.** Local/session memory is
  bounded (≤ 200 lines / ~2 KB) and organized as a lightweight recall index
  separating permanent invariants (`[PINNED]`) from time-bound entries with
  absolute dates (`[EXPIRES: YYYY-MM-DD]`). Substantive findings or oversized
  bodies spill into PR-gated artifacts (`docs/marc/` or consumer workspace) and
  are fetched on demand, never loaded unconditionally.
- **No self-merge; independent review.** Every PR gets a security pass; the author can't
  self-approve.

## Concurrent operators on one clone
Harnesses are **board-mediated peers** (#202) — two `@techlead` operators (different
harnesses, or two sessions) may run against the same clone at once. There is no
supervisor between them; these four rules are the whole coordination protocol (#204,
#205):
- **The assignee field is the claim token, not Status.** Assign yourself
  (`gh issue edit <N> --add-assignee @me`) *before* dispatching, then set Status to
  `In Progress`. Only the assignee carries operator identity: Status is a shared enum
  with no author, so it can say an item is claimed but never *by whom* — and
  `board.py reconcile` doesn't surface assignees on the board-configured path
  (`core/scripts/board.py:417`). Verify with `gh issue view <N> --json assignees`.
  GitHub's own coding agent claims work this way; Renovate does **not** — it has no
  claim field at all and serializes with external CI locks plus a per-instance work
  directory, so it is precedent for single-writer *outcome*, not for this mechanism.
- **The claim is racy, knowingly.** `board.py::set_status` is read-check-act with no
  compare-and-swap, so simultaneous claims silently last-write-wins. This is **accepted,
  not deferred**: GitHub's GraphQL exposes no optimistic-concurrency field on
  `UpdateIssueInput` or `UpdateProjectV2ItemFieldValueInput` (verified against the live
  schema), so there is nothing to adopt and closing it means building external locking.
  Re-read the *assignees* after claiming. If you aren't alone, break the tie
  deterministically — the **lexicographically lowest login keeps the item**, the rest
  unassign and re-pick. Never both-drop: both racers compute the same answer from the
  same read, and a mutual drop stalls the item nobody then owns.
- **Stale claims are reclaimed by a human, never by a timer.** An item sitting
  `In Progress` with no linked PR is *not* self-evidently abandoned — TTL reapers
  misfire on slow-but-alive workers. Surface it and ask; don't auto-steal. Where you
  don't control the peer operator, a claim that never clears is a **squat** — escalate,
  don't race it.
- **Isolate-parallel-writers extends to the operators themselves**, not just to the
  specialists they dispatch: any operator that will mutate files takes its own worktree,
  and two of them never share a branch or working tree. One `.git` hosts many worktrees
  cross-harness (verified).

## Constraints
- **Anti-anchoring / genericization (hard gate):** everything under `harnesses/` must
  stay **stack-agnostic** — zero references to any consuming repo's stack. CI enforces
  this with a grep gate. Repo-specific facts belong in the *consuming* repo's
  `AGENTS.md`/`.agents/team.toml`, read at runtime — never hardcoded here.
- **Keep this file minimal.** Record only what isn't discoverable by reading the repo.
  If agents repeat a mistake, tighten the linter/CI/test — don't grow prose here. LLMs
  anchor on whatever sits in context, including deprecated caveats.
- **An installed plugin is immutable from the user's side.** Product changes flow only
  through releases: bump `harnesses/claude-code/marc/.claude-plugin/plugin.json`
  `version` + `CHANGELOG.md`; users get it via `claude plugin update marc@nexaduo`.
  Never edit the plugin cache; auto-update is OFF for third-party marketplaces.
- **Self-improvement is context-gated:** flush-to-source is allowed ONLY in this repo.
  When mARC runs *installed* in a user's repo, lessons persist **locally** (their
  `AGENTS.md`/`team.toml`/memory); product-level lessons become **opt-in,
  human-approved, sanitized** upstream PRs — never autonomous, never leaking user
  context. This machinery lives in a dedicated `@scribe` agent, not the tech-lead skill.
- **No silent file writes** in any repo. Onboarding (`/marc:init`) is opt-in and shows
  content before writing.
- **Durable team artifacts (PEF, #46): `docs/marc/`, operator-materialized, PR-gated.**
  `@research` briefs / `@sec` reports / decision records worth keeping land in
  `docs/marc/` per its README (`YYYY-MM-DD-<type>-<slug>.md`). That folder is served
  **publicly** by GitHub Pages — nothing sensitive goes there, ever. `@sec`/`@research`
  stay strictly comment-only; the operator (`@techlead`) copies the issue comment into
  the file and lands it via a reviewed PR — never a direct commit, never a write
  carve-out for read-only agents. This binding is THIS repo's; consumer repos pin their
  own via `team.toml` (`workspace_dir`) / their `AGENTS.md`.
- **Zero-config is a feature:** the team must work in any repo with no `team.toml`
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
  `SessionStart` hook injects `team.toml` and nudges on outdated versions.

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
