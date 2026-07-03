# Contributing to mARC

mARC ships as a **portable Claude Code plugin** that runs inside *other people's*
repos. That shapes how contributions work — especially the one contribution type
this document exists for: the **field-lesson**.

## What is a field-lesson?

A field-lesson is a generalizable improvement to the agent team that you learned
while *using* mARC in your own repo — a better dispatch rule, a sharper validation
gate, a recurring constraint worth baking into an agent's prose, a workflow fix.

mARC's tech-lead skill captures lessons in **two tiers** (see
`harnesses/claude-code/marc/skills/tech-lead/SKILL.md`, section 6):

- **Tier 1 — local (default).** Every lesson lands *first* in the targets you own
  in your own repo (`AGENTS.md`, `.claude/team.config`, personal memory). This is
  automatic and stays entirely inside your repo. **Nothing leaves your repo at
  Tier 1.**
- **Tier 2 — upstream (opt-in).** When a lesson would help *every* mARC user, the
  operator may **offer** to propose it upstream to this repo. This only ever
  happens as an explicit, human-approved escalation. It is **never autonomous.**

This document is about **Tier 2** — how a field-lesson becomes a PR here.

## The non-negotiables

1. **Opt-in and human-approved, always.** A field-lesson is proposed only after
   *you* say yes, and only after you review the exact diff and PR body the agent
   generated. The agent never opens an upstream PR on its own.
2. **Sanitized — send the lesson, not your context.** Before anything leaves your
   repo it must be **generalized**: scrub repo/org/user names and slugs, absolute
   paths, hostnames, internal IDs, secrets, and any domain detail specific to your
   product. If a lesson can't be expressed without your local context, it isn't
   upstream-worthy — keep it Tier 1. The PR template's sanitization checklist is
   mandatory.
3. **Fork-based, under your own identity.** Contributions come in as a
   **fork-based pull request** opened with your own `gh` identity
   (`gh repo fork` → branch → `gh pr create`), labelled `field-lesson`. You do not
   need write access to this repo.
4. **Zero auto-merge.** No field-lesson merges automatically. Every one is
   reviewed by:
   - **CI** — the structural + install gates (Tier 1/2, no secrets),
   - **@sec** — security review of the diff (this path is a data-egress + prompt-
     injection surface; changes to skills/agents get the highest scrutiny),
   - **a human maintainer** — final approval.
5. **High bar for skill/agent prose.** Changes under
   `harnesses/claude-code/marc/skills/**` and `harnesses/claude-code/marc/agents/**`
   are the plugin's *behavior*. A malicious or sloppy edit there ships to every
   consumer, so these changes must be minimal, clearly justified, and pass the
   **anti-anchoring gate** (no consumer/this-repo specifics in agent/skill prose —
   see `.github/workflows/ci.yml`).

## Who may contribute (pilot scope)

Right now the upstream field-lesson channel is a **pilot open to NexaDuo org
members only.** The skill cannot verify org membership, so this is a policy, not
an enforced gate: if you are **not** an org member, please keep your lesson
**local (Tier 1)** and, if you'd like to share it, open a regular **issue** rather
than a PR.

Whether to widen the pilot to **anyone-via-fork** is a scheduled decision —
tracked in **issue #25** (checkpoint ~2026-07-17). Until that lands, non-member
PRs may be closed with a pointer back to the issue.

## How to open a field-lesson PR

1. Let the tech-lead skill capture the lesson locally (Tier 1) as usual.
2. When it offers to escalate — or when you ask it to — say **yes**.
3. Review the **generalized diff + PR body** it produces. Confirm the
   sanitization checklist. This is your approval gate: nothing is submitted until
   you approve the exact text.
4. It forks this repo (or you do), pushes a branch, and opens a PR labelled
   `field-lesson` using the field-lesson PR template
   (`.github/PULL_REQUEST_TEMPLATE/field-lesson.md`).
5. Address CI, @sec, and maintainer feedback. A maintainer merges — never the
   automation.

## Fork CI & secrets

The merge-gate CI (`.github/workflows/ci.yml`, Tiers 1 & 2) is **deliberately
no-secret** and runs safely on fork PRs. The secret-bearing Tier 3 headless-eval
workflow (`.github/workflows/execution-eval.yml`) is **`workflow_dispatch`-only**
and therefore **never** runs on `pull_request` — a fork PR can never reach
`ANTHROPIC_API_KEY`. Please do not add a `pull_request` trigger to that workflow.

## Other contributions

Bug reports, docs, and non-lesson code changes are welcome as normal issues/PRs.
The field-lesson rules above apply specifically to changes proposed from a
consumer repo via the tech-lead skill's upstream channel.
