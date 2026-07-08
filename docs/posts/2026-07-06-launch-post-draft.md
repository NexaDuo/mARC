# Launch post — drafts and published links (issue #57)

Working file for the developer-facing launch blog post. **Draft v3 is current**
(full-product launch, humanized prose, no em-dashes). v1 kept at the bottom for
reference, old style.

## Published (2026-07-06)

- dev.to: https://dev.to/alexandremachado/marc-irc-style-ai-agent-team-for-claude-code-that-runs-its-own-repo-22kk
- Medium: https://medium.com/@alexandre-machado/my-ai-team-has-a-tech-lead-a-board-and-a-security-gate-it-cant-skip-01b6b8904377

## Title options (safest → boldest)

1. mARC: an IRC-style AI agent team that runs my repos, and its own
2. I stopped prompting an AI to code and started running an AI team
3. My AI team has a tech lead, a board, and a security gate it can't skip

Show HN title (separate submission, links to the repo, not the post):

    Show HN: mARC – IRC-style AI agent team for Claude Code that runs its own repo

---

## Draft v3 — product launch (current)

I've spent the last months running my repos with an AI team instead of an AI
assistant. Today I'm releasing the thing that made it work: mARC, an IRC-style
agent team for Claude Code.

The problem first. A single AI coding session is a brilliant engineer with no
memory, no process, and no colleagues. It writes code fast and loses track
faster. Decisions evaporate when the chat ends. Work happens that no board ever
sees. And the same session that wrote the code is the one that reviews it,
which means nobody reviews it. A smarter model doesn't fix any of this. Teams
weren't invented because engineers were dumb. They were invented because
shipping needs structure.

So mARC gives the AI a team structure instead of a bigger brain. When you open
the channel you talk to `@techlead`, an operator that behaves like a tech lead,
not like a code generator. Five specialists idle in the channel until pinged:
an engineer, an SRE, a designer, a security reviewer, and a researcher. You say
"users are getting pinged when we save issues, fix it". The tech lead writes
the spec, opens a GitHub issue, puts it on the Project board, dispatches the
engineer in the background, and keeps talking to you while the work runs.

Four rules turn this into a team instead of a fan-out script.

**Specs before dispatch.** A vague task produces a vague PR. The operator won't
delegate anything that lacks a goal, acceptance criteria, affected files, and
constraints. If something's missing it asks you first, because one question now
is cheaper than a reverted PR later.

**The board is the source of truth, and it gets audited.** Work lives in GitHub
Issues and a Project board, not in the chat scrollback. Before dispatching
anything, the operator checks the board against reality: merged PRs, deploys,
CI. Boards lie. Merged PRs don't. Status actually means something here. Blocked
means it needs you. Done means merged and validated.

**Nothing merges without a review the author can't skip.** Every PR gets a
security pass on its diff before merge. The reviewers are read-only by
contract: the security agent and the researcher physically have no write tools.
At one point we considered giving them a scoped write folder. The researcher
went off, read OWASP's LLM guidance and GitHub's token-hardening docs, and came
back recommending against its own write access. The idea died in review, where
it belonged.

**The process itself is versioned.** When something goes wrong, the lesson
doesn't die in the chat. It lands in the playbook via PR and gets reviewed like
any code. The team that made the mistake ships the rule that prevents it.

Does this actually work? mARC runs its own repository, and every issue, PR,
security review, and decision record of the team building the team is public.
Including the embarrassing ones.

My favorite: the team's own issue template was writing handles like `@dev` into
GitHub issue bodies. Those are real GitHub accounts owned by real strangers,
and they got mentioned on every issue we filed. One release even listed them as
contributors. The team fixed the template, sanitized 14 historical issues, and
added a CI gate so it can't happen again. Then the security review of that fix
caught that the gate was case-sensitive, so `@Dev` would have slipped through.
One grep flag. That's what a second pair of eyes is for.

There's more in the record. A config migration where the security reviewer
probed the new parser with hostile values and got two hardenings merged before
the PR landed. A day when I dispatched two writing agents in parallel without
isolating their worktrees and one clobbered the other's checkout. The recovery
and the dispatch rule that came out of it are both public.

To be clear about what this is not: it's not the demo where an agent writes a
TODO app in one take. It's slower and noisier than that, on purpose. Specs
before dispatch, review before merge, and a paper trail a human can audit six
months later. The board isn't a visualization of what the AI did. It's the real
board. I can grab any item on it and do it myself.

**Try it in two minutes.** You need Claude Code and a GitHub repo:

```
/plugin marketplace add NexaDuo/mARC
/plugin install marc@nexaduo
/marc:tech-lead
```

No config, no signup. It discovers your repo at runtime: org, board, paths. If
you want the binding to persist across sessions, `/marc:init` scaffolds a
`team.toml`, shows you every file before it writes anything, and writes nothing
without your yes.

The repo is the real landing page: **[github.com/NexaDuo/mARC](https://github.com/NexaDuo/mARC)**. The whole
history of an AI team running itself is in there, embarrassments included.

---

## Draft v1 — features-of-the-week angle (superseded, kept for reference; old style)

Last Sunday, my AI team shipped four releases: it escaped a bug that was pinging
strangers on GitHub, migrated its own config format to TOML, fixed misleading
analytics copy on its landing page, and blocked two of its own PRs until security
advisories were addressed. I mostly watched, answered four questions, and merged
nothing by hand.

The team is called mARC. It's a plugin for Claude Code that turns one AI session
into an IRC-style channel: a `@techlead` operator runs the conversation, and five
specialists idle in the channel until pinged — an engineer, an SRE, a designer, a
security reviewer, and a researcher. You talk to the tech lead like you'd talk to
a tech lead: "users are getting pinged when we save issues, fix it." It writes the
spec, opens the issue, puts it on the GitHub Project board, dispatches the
engineer in the background, and stays responsive while the work runs.

The part I care most about is the part that's boring on purpose: **process**.
Nothing merges without a security review of the diff. The reviewers are read-only
by contract — the security agent and the researcher physically have no write
tools, and when we considered giving them a scoped write folder, the researcher
itself surveyed OWASP's LLM guidance and GitHub's token-hardening docs and
recommended against it. The board is reconciled against reality before anything
is dispatched, because boards lie and merged PRs don't.

Does it work? Here are the receipts, all public in the repo:

- **The stranger-pinging bug.** Our issue template wrote team handles like
  `@dev` and `@sec` into GitHub issue bodies. Those are real GitHub accounts
  owned by real strangers, who got mentioned on every issue we filed — and one
  release even listed them as "contributors". The fix shipped the same day:
  template escaped, 14 historical issue bodies sanitized, and a CI gate that
  greps the template so it can't regress. The security review of that fix found
  the gate was case-sensitive (`@Dev` would slip through) — one flag, fixed
  before merge.
- **The config migration.** We moved the per-repo binding from a homegrown
  key=value file to TOML in one day: schema, hooks, skills, CI contract test
  that checks the shell extraction agrees with a real TOML parser. The security
  pass probed the sed-based parser with hostile values (`$(...)`, backticks,
  leading dashes) and got two hardenings merged before the PR landed.
- **The collision.** I dispatched two writing agents in parallel without
  isolating their worktrees. One switched the shared checkout's branch mid-task
  and stashed the other's edits. The second agent noticed, created its own
  worktree, recovered its work, and finished. The lesson is now in the
  operator's process buffer, waiting to be flushed into the playbook — which is
  itself how this team learns: mistakes become versioned process, via PR, like
  everything else.

None of this is the demo where an agent writes a TODO app in one take. It's
slower and noisier than that, and that's the point: specs before dispatch,
review before merge, and a paper trail a human can audit six months later. The
board isn't a visualization of the AI's work — it's the actual source of truth,
and I can grab any item and do it myself.

**Try it in two minutes.** You need Claude Code and a GitHub repo:

```
/plugin marketplace add NexaDuo/mARC
/plugin install marc@nexaduo
/marc:tech-lead
```

That's it — no config, no signup. It discovers your repo at runtime. If you want
the binding to persist across sessions (board number, paths, validation
command), `/marc:init` scaffolds a `team.toml`, shows you every file before
writing, and writes nothing without your yes.

The repo is the real landing page: every issue, PR, security review and decision
record from the team running itself is public at **[github.com/NexaDuo/mARC](https://github.com/NexaDuo/mARC)** —
including the ones where it embarrassed us.

---

## Cross-posting plan (from the channel research on issue #57)

**Medium** (tags, in this order):
`Programming` (13.7M followers), `Artificial Intelligence` (9.2M), `AI Agent`
(479K followers vs only 42K stories: best relevance-to-competition ratio),
`Software Development` (5.8M), `Claude` (small but active niche).
Skip on Medium: Developer Tools, Open Source, Github (tiny follower bases there).
Publication: Better Programming is on hiatus since Nov 2023. Level Up Coding is
the plausible target but its guidelines/reach could not be verified from a
logged-out session; check from your logged-in account, otherwise publish on
your personal profile.

**dev.to** (exactly 4 tags):
`ai, productivity, opensource, github` (post volumes: ai 295K, productivity
151K, opensource 80K, github 26.5K). Swap `github` for `programming` if raw
reach matters more than precision.
Mechanics: set `canonical_url` in the front matter to the personal-blog URL
(protects your blog's SEO; a documented case showed cross-posting without it
cannibalizing a small personal blog).

## Assembly notes (not part of the post)

- Evidence calls for a GIF of the channel + board: record an asciinema/screencast
  of `/marc:tech-lead` dispatching a real item.
- Show HN links the REPO, not the post; be available ~2h after submitting to
  answer comments (≈95% of upvotes land in the first ~2h).
- Timing (weak-moderate evidence): Sun–Tue, ~8–11am US Eastern.
- Kill-list check before publishing: no salesy tone, no vague title, no signup
  wall, no vote coordination, no LLM-sounding prose (em-dashes included).
- v3 claim check before publishing: "I've spent the last months running my
  repos with an AI team" must match your real usage window.
