## Issue body template

```markdown
## Goal
<one paragraph: the outcome and why it matters>

## Context
<relevant background from the discussion; links to code / AGENTS.md>

## Acceptance criteria
- [ ] <observable, testable condition>
- [ ] ...

## Affected surface
- `<path/or/service>` — <what changes>

## Constraints & lessons (repo AGENTS.md)
- <e.g. reproducibility: fix must land in IaC, no manual drift>

## Release & validation (per repo AGENTS.md; mark N/A if greenfield)
- [ ] Deploy to staging
- [ ] E2E/smoke validation in staging (real URLs)
- [ ] Deploy to production
- [ ] E2E/smoke validation in production (real URLs)
- [ ] CI workflows monitored to green

## Regression test
- [ ] End-to-end test in the repo's suite — OR justification why N/A

## Assignee
`@<dev|sre|design|sec|rev|research>`
```

(Note the backticks around the assignee handle: team handles collide with real
GitHub usernames, so every handle in an issue/PR body must be escaped.)

---

