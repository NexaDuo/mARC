<!--
  FIELD-LESSON PR TEMPLATE (issue #22)
  Use this template when proposing a generalizable improvement (a "field-lesson")
  learned while using mARC in your own repo. See CONTRIBUTING.md.
  This channel is OPT-IN, SANITIZED, FORK-BASED, and NEVER auto-merged.
  To select this template add ?template=field-lesson.md to the PR "compare" URL,
  or apply the `field-lesson` label.
-->

**Type:** field-lesson (upstream contribution — issue #22)

## The lesson (generalized)

<!-- State the improvement in generic, product-level terms. Send the LESSON, not
     your local context. What behavior should every mARC user get, and why? -->

## Why it generalizes

<!-- Why does this help every mARC user, not just your repo? If it can't be
     expressed without your local specifics, it is NOT upstream-worthy — keep it
     local (Tier 1) and close this PR. -->

## What changed

<!-- Which files? Skill/agent prose changes (skills/**, agents/**) get the highest
     scrutiny — keep them minimal and justified. -->

## Sanitization checklist (required — all must be true)

- [ ] No repo, org, or user **names or slugs** (mine or anyone's) in the diff/body.
- [ ] No **absolute paths**, hostnames, URLs, or environment-specific locations.
- [ ] No internal **IDs**, ticket numbers, board/project identifiers, or customer names.
- [ ] No **secrets**, tokens, keys, credentials, or anything sensitive.
- [ ] No **domain/product detail** specific to my repo — the change is generic.
- [ ] Agent/skill prose still passes the **anti-anchoring gate** (no consumer/
      this-repo specifics in `agents/**` or `skills/**`).
- [ ] This diff + PR body is **exactly** what I reviewed and approved — the agent
      submitted nothing I did not see.

## Consent & scope

- [ ] I explicitly opted in to propose this upstream (Tier 2). It is **not**
      autonomous — I approved the exact diff and this body.
- [ ] The lesson is (or will remain) captured **locally (Tier 1)** in my own repo;
      this PR is additive.
- [ ] I am a **NexaDuo org member** (current pilot scope — see CONTRIBUTING.md and
      issue #25). If not, I understand this PR may be closed with a pointer to keep
      the lesson local.

## Review expectations

- [ ] I understand this will **not** auto-merge and requires **CI + @sec review +
      human-maintainer approval** before landing.
