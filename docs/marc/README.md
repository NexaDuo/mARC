# `docs/marc/` — durable team artifacts (PEF)

This folder is the mARC team's **persistent workspace** for durable artifacts:
the compiled outputs of specialist work that the team must find again later,
versioned with the code instead of buried in issue threads. It implements the
file-write policy (PEF) decided in
[issue #46](https://github.com/NexaDuo/mARC/issues/46), based on the
`@research` brief there (ADR canon: keep decision records in source control).

## ⚠️ PUBLIC — this folder is a website

**Everything in `docs/` — including this folder — is served publicly by GitHub
Pages (marc.nexaduo.com). Every file here is public web content by
construction, on push, immediately.** This trade-off was accepted knowingly in
#46. Therefore:

- **Nothing sensitive ever goes here.** No secrets, tokens, hostnames of
  private systems, personal data, unpublished vulnerability details, or any
  consumer-repo context. If in doubt, it stays in the issue comment.
- Treat writing a file here as publishing it on the open web, because it is.

## What belongs here

- **Research briefs** — the cited evidence briefs `@research` posts on issues.
- **Security reports** — durable `@sec` review summaries worth keeping
  (sanitized: findings already fixed, no live exploit detail).
- **Decision records** — ADR-style records of operator/user decisions.

## What does NOT belong here

- Ephemeral discussion, status updates, back-and-forth — that stays in issue
  comments (their natural home).
- Anything sensitive (see the warning above).
- Process lessons for agents — those go to `AGENTS.md`, not here.

## Naming convention

```
YYYY-MM-DD-<type>-<slug>.md
```

- `YYYY-MM-DD` — the date the artifact was produced (not landed).
- `<type>` — one of `brief` (research brief), `secreport` (security report),
  `decision` (decision record).
- `<slug>` — short kebab-case topic.

Example: `2026-07-06-brief-team-artifacts-file-write-policy.md`.

Superseded artifacts are kept and marked superseded, never deleted (ADR
practice).

## Landing process (write policy)

- `@sec` and `@research` are **strictly read-only / comment-only**. They never
  write files here — their deliverable is the issue comment. No write
  carve-outs for read-only agents (OWASP LLM06 least-privilege; see the #46
  brief).
- The **operator (`@techlead`) materializes** the artifact: copies the issue
  comment into a file here (attributing the producing specialist and linking
  the motivating issue) and lands it **via a reviewed PR** — never a direct
  commit.
