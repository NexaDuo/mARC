# Research brief: loop-engineering and agent-harness-engineering concepts for mARC

**Produced by:** `@research` · **Issue:** [#153](https://github.com/NexaDuo/mARC/issues/153)
**Materialized by:** `@techlead`, per the durable-artifact policy ([#46](https://github.com/NexaDuo/mARC/issues/46))

## Question

Which agent-harness-engineering and loop-engineering concepts, from external
practitioner writing, are worth incorporating into mARC's orchestration model?

## Sources read in full

- Addy Osmani, "Agent Harness Engineering," Apr 19 2026 —
  https://addyosmani.com/blog/agent-harness-engineering/
- Addy Osmani, "Loop Engineering," Jun 7 2026 —
  https://addyosmani.com/blog/loop-engineering/
- Leanware, "Ralph Wiggum in AI Coding," Jan 28 2026 —
  https://leanware.co/insights/ralph-wiggum-ai-coding
- Firecrawl, "Loop Engineering," Jun 11 2026 —
  https://www.firecrawl.dev/blog/loop-engineering

## TL;DR

The four sources converge on a small set of ideas: harnesses/loops win on
stop conditions (hard cap + no-progress check + spend cap), maker/checker
separation (a different, not necessarily smarter, verifier), durable external
state (repo/board, not agent memory), and bounded, sandboxed execution. mARC
already embodies most of the load-bearing ones. The raw unbounded "Ralph
Wiggum" loop is the antithesis of mARC's bounded-dispatch rule, and mARC's
opt-in-only reconcile stance conflicts with "scheduled automations." The
genuinely new candidates are narrower: an explicit no-progress/diff
stop-check, tool-output offloading for long dispatches, and a tightly scoped,
guarded mini-Ralph loop for mechanical, test-verifiable fixes.

## Key findings — already in mARC

- Harness = model + scaffolding (bounded dispatch, sonnet-default, review
  gates, token sentinel) maps to mARC's whole premise.
- The "ratchet" (every observed mistake becomes a permanent rule) maps to the
  `(origin: #NN · date)`-tagged rule mechanism and the operating-invariants
  card ([#41](https://github.com/NexaDuo/mARC/issues/41)).
- Planner/generator/evaluator split, self-grading being unreliable, maps to
  the mandatory `@sec` AND `@rev` pre-merge gate
  ([#125](https://github.com/NexaDuo/mARC/issues/125)) — a distinct,
  read-only reviewer role that cannot be the PR author.
- The "sprint contract" (negotiate the done-condition before code is
  written) maps to the tech-lead's sufficiency gate before dispatch.
- Durable external state (re-read from filesystem/git/board, not agent
  memory) maps to the GitHub Project board as source of truth and reconcile
  ([#123](https://github.com/NexaDuo/mARC/issues/123)).
- Worktree isolation for parallel agents predates and matches mARC's
  isolate-concurrent-mutating-dispatches rule.
- Delegate execution to subagents (the orchestrator doesn't run the loop
  itself) matches mARC's delegate-execution rule
  ([#81](https://github.com/NexaDuo/mARC/issues/81)) almost word for word.
- Progressive disclosure via Skills is already mARC's own architecture
  (`skills/*/SKILL.md`), not merely a candidate to adopt.

## Key findings — candidates and rejections

- **Candidate adopted:** stop-condition triad (iteration cap + no-progress
  check + spend cap). mARC had two of three (tool-call budget, token
  sentinel origin [#119](https://github.com/NexaDuo/mARC/issues/119)) but no
  codified "stop when nothing is changing" check.
- **Candidate, design-first:** tool-output offloading against context rot
  (write large output to disk, keep head/tail + path in context). Tracked
  separately as [#157](https://github.com/NexaDuo/mARC/issues/157) —
  design-first, gated on a posted proposal before any SKILL.md edit.
- **Candidate adopted, guarded:** a tightly scoped mini-Ralph loop for
  mechanical, test-verifiable fixes inside one bounded dispatch.
- **Rejected — conflicts with #69:** the raw unbounded "Ralph Wiggum" loop
  (`while :; do cat PROMPT.md | claude-code; done`, no memory, no stop
  condition). Adopting it wholesale would violate mARC's standing
  bounded-dispatch rule; only the guarded, narrower variant is compatible.
- **Rejected — conflicts with #123:** scheduled/cadence automations for
  proactive discovery and triage. mARC's reconcile rule is explicitly the
  opposite: only three triggers, never session start, recovery sweeps stay
  opt-in and user-requested.

## Coverage and confidence

All four sources were fetched and read in full; no source reports a measured
benchmark — every claim is the source's own reported practice or stated
opinion, not a shown experiment. Confidence: moderate-high for the mapping
against mARC's existing rules, lower for follow-up sizing (the brief's own
inference). Full findings-by-source detail lives in the original brief:
https://github.com/NexaDuo/mARC/issues/153#issuecomment-5040182060

## Resulting actions

- [#154](https://github.com/NexaDuo/mARC/issues/154) — no-progress/no-diff
  stop-check, shipped as a governed rule in `skills/tech-lead/SKILL.md`.
- [#155](https://github.com/NexaDuo/mARC/issues/155) — guarded mini-Ralph
  loop, shipped as a governed rule in `skills/tech-lead/SKILL.md`, framed as
  a scoped exception to #69.
- [#156](https://github.com/NexaDuo/mARC/issues/156) — both rejected
  patterns recorded in `skills/tech-lead/references/invariants-card.md`,
  cross-referenced from SKILL.md's #69 and #123 rules.
- [#157](https://github.com/NexaDuo/mARC/issues/157) — tool-output
  offloading, tracked as a separate design-first issue; no SKILL.md edit in
  this pass to avoid colliding with the combined PR above.
