### 1. Compile the demand
Synthesize the conversation into a concrete list of deliverables. Group by
discipline (engineering / SRE / design / security). For each item, state the
**outcome**, not just the task.

### 2. Reflect on sufficiency — the gate before delegation
Before you create or dispatch anything, ask: *if I handed this to someone with
zero chat context, could they execute it correctly?* A task is ready only with:
- **Goal & context** — why this matters, what it unblocks.
- **Acceptance criteria** — observable, testable "done" conditions.
- **Affected surface** — concrete files/services/dirs from the repo's
  AGENTS.md/team.toml (never invented).
- **Constraints** — applicable AGENTS.md items (reproducibility, protected
  data stores, tooling AVOID lists, config model).
- **Mandatory release phases** — the repo's documented phases with real URLs,
  CI monitored to completion; say so explicitly if greenfield.
- **Regression test** — mandatory for bug fixes unless pure infra/CLI/internal
  logic; justify any skip.

<!-- rules:origin-required -->
- **Cross-service contract tests must traverse the real producer path.** For
  any field one service writes and another reads, the mandatory regression
  test must go through the **actual** serializer/payload builder that produces
  it in production, never a hand-assembled fixture that hard-codes the
  discriminator. A hand-built payload can stay green while the real client
  stops emitting the field it asserts on — "reviewed ≠ executed" catches the
  review gap, this closes the matching test gap. State this explicitly in the
  issue's acceptance criteria when the task touches a cross-service contract.
  (origin: #134 · 2026-07-20)
<!-- /rules:origin-required -->

If any item is underspecified, ask the user now (AskUserQuestion for genuine
decisions). Never delegate a vague task — it produces a vague PR.

### 3. Record on the team board (source of truth = GitHub Project)
For each ready item, run the bundled `create` command — one call replaces the
`gh issue create` + `gh project item-add` + set-status sequence:
```bash
python3 "${{{ plugin_root_env }}:-.}/scripts/board.py" create \
  --title "<type>: <concise outcome>" \
  --body-file <path-to-the-detailed-body-from-the-template-below> \
  --labels "<discipline-and-severity labels, comma-separated>" \
  --status "Todo"
```
Prefer existing labels. Degrades gracefully on the board-add/status steps
(missing scope, unconfigured board): the issue is never lost, only a
`board_added: false` warning surfaces — follow up manually rather than assume
it landed.

#### Board status convention (keep it honest, reflect reality)
- **Todo** — triaged, not started. **In Progress** — set the moment you
  dispatch it. **Done** — only after merged **and** validated (step 5).
- **Blocked** — needs the user's action/decision (external system, credential,
  approval, strategy call); say exactly what you need, never leave it
  "In Progress" pretending work is happening.

Run the bundled `set-status` command — one call replaces the
field-list/item-list/item-view/item-edit sequence:
```bash
python3 "${{{ plugin_root_env }}:-.}/scripts/board.py" set-status \
  --issue <N> --status "<Todo|In Progress|Blocked|Done>"
```
Validates against the project's real Status options; FAILS LOUDLY (never
no-ops) if unresolvable — a non-zero exit means fix the board, don't move on.

#### Recording discipline (rule origin + sanitization)
<!-- rules:origin-required -->
- **Tag every governed rule with its origin** `(origin: #NN · YYYY-MM-DD)`.
  Fenced regions (`<!-- rules:origin-required --> … <!-- /rules:origin-required -->`)
  are CI-gated: a PR fails if any fenced rule lacks a tag. (origin: #68 · 2026-07-13)
- **Sanitize before recording on a PUBLIC tracker** — a consumer's PRIVATE-repo
  client details stay in a private team note; the public board gets only
  sanitized findings. (origin: #66 · 2026-07-09)
- **Size-capped memory writes — oversized items become PR-gated artifacts.**
  Local memory entries (e.g. `MEMORY.md`, session notes) must stay strictly
  compact (≤ 200 lines / ~2 KB total; tool excerpts ≤ 2 KB). Never dump raw
  logs, diffs, or full briefs into memory. Any finding, research brief, or
  decision exceeding the cap must be materialized as a durable artifact in the
  repo's team-artifacts workspace (`docs/marc/` or consumer workspace) and landed
  via a reviewed PR (PEF, #46). Memory retains only a 1-line index reference to
  the artifact. (origin: #176 · 2026-07-29)
- **Memory durability: Pinned vs. Decay with absolute ISO date expiry.**
  Every persisted memory entry must declare its durability class: `[PINNED]` for
  permanent invariants and architectural constraints (never decay; retired only
  via explicit superseding PRs), or `[EXPIRES: YYYY-MM-DD]` with a strict ISO
  absolute date for transient workarounds, temporary flags, or in-flight notes.
  Never use relative expiry ("in 2 weeks"). When reading memory, disregard any
  entry where `current_date > expiry_date`; prune expired entries
  opportunistically during buffer-flush or maintenance passes. (origin: #176 · 2026-07-29)
- **Two-tier recall index — index first, fetch detail on demand.** Structure
  memory as a lightweight recall index (1-line topic descriptor + trigger
  condition + path pointer) rather than an always-loaded prose blob. Read full
  memory bodies or referenced artifacts via `Read` only when the index indicates
  relevance to the active task. (origin: #176 · 2026-07-29)
<!-- /rules:origin-required -->

