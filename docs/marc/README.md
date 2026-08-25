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

## Artifacts

- [2026-07-06-brief-team-artifacts-file-write-policy.md](2026-07-06-brief-team-artifacts-file-write-policy.md) — the `@research` brief behind this folder's own PEF policy (#46).
- [2026-07-06-decision-team-config-toml.md](2026-07-06-decision-team-config-toml.md) — decision record: per-repo team binding moves from `team.config` to `team.toml` (#51).
- [2026-07-21-brief-agy-marketplace-clone-free-install.md](2026-07-21-brief-agy-marketplace-clone-free-install.md) — research brief: no clone-free `agy` (Antigravity) marketplace install exists (#151).
- [2026-07-21-brief-loop-engineering-guardrails.md](2026-07-21-brief-loop-engineering-guardrails.md) — research brief on loop-engineering guardrails (#154).
- [2026-07-24-decision-distribution-channel-marketplace-only.md](2026-07-24-decision-distribution-channel-marketplace-only.md) — decision record: the Claude Code plugin marketplace remains mARC's only supported install channel; `skills.sh`, `npx claude-plugins`, and a self-owned `npx` installer considered and rejected (#159, #160, #161).
- [2026-07-29-decision-ai-memory-rejected.md](2026-07-29-decision-ai-memory-rejected.md) — decision record: mARC does not adopt `akitaonrails/ai-memory` (install-channel conflict, no PR-review gate on its writes); three of its conventions tracked separately as a follow-up (#175, #176).
- [2026-08-12-decision-context-advisory-retired.md](2026-08-12-decision-context-advisory-retired.md) — decision record: the token sentinel's context-size advisory is retired (not fixed) — Claude Code's own harness already auto-compacts against the real per-model window; the runaway-loop (#71) and model-switch (#73) guards survive (#181).
- [2026-08-12-brief-harness-context-management.md](2026-08-12-brief-harness-context-management.md) — research brief: how Claude Code, Antigravity (`agy`) and Copilot CLI each handle context windows and auto-compaction natively. Claude Code and Copilot CLI have confirmed native mechanisms; Antigravity's could not be confirmed from primary sources, which is the gap tracked in #186 (#181).
- [2026-08-24-brief-agy-context-compaction.md](2026-08-24-brief-agy-context-compaction.md) — research brief: Google Antigravity (`agy`) context compaction & harness parity findings (#186).
- [2026-08-25-decision-cross-harness-dispatch-direction.md](2026-08-25-decision-cross-harness-dispatch-direction.md) — decision record: no nested cross-harness dispatch bridge in either direction; harnesses stay board-mediated peers. Both CLIs are capable callees, but neither vendor can supervise the other's process (no completion notification, no cost observability), and the `@sec`/`@rev` gate is already vendor-agnostic (#202).

## Landing process (write policy)

- `@sec` and `@research` are **strictly read-only / comment-only**. They never
  write files here — their deliverable is the issue comment. No write
  carve-outs for read-only agents (OWASP LLM06 least-privilege; see the #46
  brief).
- The **operator (`@techlead`) materializes** the artifact: copies the issue
  comment into a file here (attributing the producing specialist and linking
  the motivating issue) and lands it **via a reviewed PR** — never a direct
  commit.
