### 5. Track to done
Summarize: demand → issue/board link → specialist → status. Dispatches run in
the background — stay responsive, resume an agent by its id for the next
dependency-chain stage. Relay PR links and CI/deploy status as specialists
report; keep board `Status` in sync. Not complete at PR-open — immediately
dispatch `@sec` and `@rev` to review the PR, monitor CI to green, and follow
through the repo's release phases to validated success.

For each re-review, send the prior reviewed SHA, current HEAD, unresolved
finding IDs (`SEC-n`/`REV-n`), and any structurally failed optional pass from the prior
handoff. Request the compact return defined in `dispatch.md`; inspect report
sections on demand. Both independent verdicts must still cover current HEAD.
Do not repeat an inconclusive optional pass merely because a new round started.
When `@rev` reports a diff touching `team.toml`'s `[review].hot_surfaces`, run
`/code-review` at `high` yourself; a subagent cannot. (origin: #125 · 2026-07-16)

**Merge gate, at every push and before any merge** (run the line whole):
```bash
S="{{ bundled_scripts_dir }}"; python3 "$S/merge_gate.py" --pr <N> --expect-reviewer sec=<id> --expect-reviewer rev=<id>
```
Exit `0` passes, `1` blocked (fix printed), `2` unreadable (never a pass).
It does not cover CI or `mergeStateStatus`: check CI separately.

**Did a version bump ship** (tag, workflow, release)? Same line,
`release_verify.py --json` in place of `merge_gate.py …`; non-zero = NOT
verified, read which check failed.

**Merge handoff requires the proof, not the assertion** — pass the verifiable
`@sec` record (the `## @sec review` comment URL), never a bare "APPROVED" from
memory. This repo's PR author can't self-approve, so `reviewDecision` is
always empty; that's expected, don't re-block on it. (origin: #105 · 2026-07-16)

<!-- rules:origin-required -->
- **Autonomously dispatch pre-merge gates immediately upon PR open.** Dispatch
  `@sec` and `@rev` in the background at once; don't ask permission. Done means
  merged and validated. (origin: #243 · 2026-09-08)
- **The pre-merge gate is `@sec` AND `@rev` AND bots-adjudicated-at-HEAD;
  `merge_gate.py` enforces it.** Both `## @sec review` / `## @rev review`
  markers, from a trusted author (association missing/untrusted fails closed),
  with `reviewed-sha:` equal to the current HEAD and a verdict; BLOCK from
  either blocks. Trust boundary: the association check filters
  non-collaborator forgeries, it does not authenticate the reviewer. Mint a
  unique `reviewer: <harness>/<dispatch-id>` per dispatch and pass it as
  `--expect-reviewer` (SHOULD: audit traceability, not authentication).
  Unresolved inline review threads block too ("CI green" is not permission):
  per thread, verify the fix, reply citing the commit (or a stated won't-fix
  justification), resolve; repeat at every push.
  (origin: #125 · 2026-07-16) (origin: #139 · 2026-07-20) (origin: #216 · 2026-10-03)
- **Re-read the operating-invariants card before tagging or merging.** Treat
  `invariants-card.md` as a checkpoint at that
  moment, not just a post-compaction reminder. (origin: #41 · 2026-07-21)
<!-- /rules:origin-required -->

**Terminal-state playbook: `REVIEW_REQUIRED` with no eligible non-author
approver** (`merge_gate.py` reports it as a blocker; no push fixes it).
<!-- rules:origin-required -->
- **Detect this early, not at merge time:** run the gate right after opening
  the PR or dispatching reviews. (origin: #133 · 2026-07-20)
- **Escalate with a named ask, never a vague "blocked":** request a specific
  human (`gh pr edit <N> --add-reviewer <user>`) or state the exact action, and
  set the board item to **Blocked** with that ask. (origin: #133 · 2026-07-20)
- **`--admin` override policy.** `gh pr merge --admin` needs explicit,
  in-the-moment human authorization for this PR, never a standing default;
  record who authorized it and why. (origin: #133 · 2026-07-20)
<!-- /rules:origin-required -->
