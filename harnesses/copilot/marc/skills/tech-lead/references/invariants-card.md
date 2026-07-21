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
