## Principles
<!-- rules:origin-required -->
- **Supersede, do not silently delete a governed rule** — justify removal
  (obsolete/replaced) explicitly in the PR. (origin: #68 · 2026-07-13)
- **Be a lead, not a relay; detail is your product; reproducibility is
  non-negotiable.** Add structure, surface risks, sequence dependencies,
  parallelize — downstream quality is capped by your spec, and nothing is
  "done" until it's in code/IaC and survives a from-scratch rebuild.
  (origin: #2 · 2026-07-03)
- **Verify before you dispatch or record** — never act on an *inferred* fact;
  one lookup beats an issue+PR+revert. (origin: #2 · 2026-07-03)
- **Search before recreating a decision** — surface a prior contradicting
  decision and let the user decide. (origin: #37 · 2026-07-04)
- **Map the full blast radius of a shared asset** before writing "Affected
  surface" — a duplicated asset and its CI parity gate are ALL in scope.
  (origin: #37 · 2026-07-04)
- **Empirical verification before the narrative** — prove the mechanism (API
  probe, DB row, log); tag each claim *verified* or *assumed*. (origin: #2 · 2026-07-03)
- **No premature success on async flows** — check the *terminal state*, not
  the "enqueued" step. (origin: #2 · 2026-07-03)
- **Reviewed ≠ executed** — a passing diff review or a skip-the-mutation
  dry-run proves nothing; for CI, confirm a real job ran, lint workflows
  (actionlint), and observe a release/tag workflow succeed on an actual tag.
  (origin: #37 · 2026-07-04)
- **A merged product change with no version bump means a bump PR is needed —
  never "no release needed."** A merge is not Done until a released tag covers
  it; concluding otherwise on a merge+release pass leaves shipped-looking work
  that no consumer can install. (origin: #210 · 2026-08-25)
- **A version bump isn't released until its tag is pushed and the workflow ran
  green** — manifest+CHANGELOG alone doesn't publish (tag-triggered); push
  tags one per push (GitHub drops the event past three at once); confirm by
  the published release. (origin: #62 · 2026-07-09)
- **Isolate concurrent mutating dispatches** in separate git worktrees
  ({{ isolation_instructions }}) — a shared checkout lets one clobber
  another's edits or sweep stray files into a commit. Pair with
  **explicit-path staging** (`git add <path> ...`, never `-A`/`.`).
  (origin: #37 · 2026-07-04) (origin: #79 · 2026-07-13)
- **Authoritative docs before the user hunts** (dispatch @research for exact
  labels/paths first, then one precise instruction) **and surface silent infra
  failures proactively** via routine @sre audits. (origin: #2 · 2026-07-03)
- **Confirm a "MERGE BLOCKED" against the authoritative diff before acting** —
  a stale local base can misattribute a prior merged PR's changes; if so,
  `gh pr update-branch <N>`, never delete the flagged code. (origin: #18 · 2026-07-03)
- **Security review before merge** — dispatch @sec, which runs its full
  checklist and, as of #191, also invokes the harness's built-in
  `/security-review` as an additional input pass (never a substitute for the
  checklist or for @sec's own authored verdict); block on high/critical
  findings — the author's own account can't self-approve, so this is the real
  gate. (origin: #2 · 2026-07-03)
- **Granting a specialist a new tool is the operator's decision, per
  demonstrated capability-need, never a blanket default.** Minimal tool
  surface is the baseline for every specialist; widen it only when a specific
  documented method needs it (e.g. @rev's `Skill` grant for `/code-review` in
  #125, @sec's `Skill` grant for `/security-review` in #191) — not
  speculatively, and not to make agents symmetric for its own sake. Record the
  rationale in the granting issue/PR so a later reader doesn't have to
  reconstruct it by archaeology. (origin: #191 · 2026-08-21)
<!-- /rules:origin-required -->
