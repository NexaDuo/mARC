# mARC operating invariants (post-compaction reminder)

Context was just compacted. These premises are the ones most prone to drift
after a summary — re-anchor before your next action:

- **Merge gate is dual and marker-based.** A PR needs BOTH `@sec` and `@rev`
  approval markers, grep-verifiable in PR review comments, before merge. No
  self-merge, no single-reviewer shortcut, no inferring approval from silence.
- **The board must reflect reality.** Before dispatching or closing, reconcile
  status against the actual PR/issue state — don't trust a stale card.
- **Branch from a freshly-fetched `origin/main`.** `git fetch origin` before
  every `checkout -b`; never branch from a local/stale `main`.
- **Verify before you dispatch or build.** Confirm IDs, ownership, and values
  empirically (Read/Grep, `gh`, DB schema) before acting on an inferred fact.
- **Never ingest file content via filtered bash.** Use `Read`/`Grep` for file
  content; `Bash` is for execution/status only.
- **Release phases run to validated done.** Staging deploy -> staging
  smoke/E2E -> prod deploy -> prod smoke/E2E, with real URLs. A PR merge is
  not "done."
- **Stage explicit paths.** `git add <path>...`, never `-A`/`.`.

## Rejected external patterns (supersede-not-delete)

Considered and explicitly rejected against a standing mARC rule — recorded
here so a future session or contributor doesn't have to re-derive the
rejection from first principles or re-propose the same pattern:

- **Raw unbounded "Ralph Wiggum" loop** (`while :; do cat PROMPT.md |
  claude-code; done` — no memory, no stop condition, runs to exhaustion).
  Conflicts with the bounded-dispatch rule: every dispatch carries stop
  criteria and a tool-call budget, never an open-ended `continue` (origin
  #69). A narrow, test-gated exception exists for mechanical fixes — the
  guarded mini-Ralph loop inside one bounded dispatch (origin #155) — that is
  a scoped carve-out of #69, not a reopening of the raw pattern. (origin:
  #153 · 2026-07-21)
- **Autonomous scheduled/cadence automation** (discovery/triage that runs on
  its own timer, unprompted). Conflicts with the opt-in-only reconcile
  stance: reconcile fires only on three explicit triggers — never session
  start, never a background sweep (origin #123). (origin: #153 · 2026-07-21)
