### 5. Track to done
Summarize: demand → issue/board link → specialist → status. Dispatches run in
the background — stay responsive, resume an agent by its id for the next
dependency-chain stage. Relay PR links and CI/deploy status as specialists
report; keep board `Status` in sync. Not complete at PR-open — immediately
dispatch `@sec` and `@rev` to review the PR, monitor CI to green, and follow
through the repo's release phases to validated success.

For each re-review, send the prior reviewed SHA, current HEAD, delta path,
unresolved finding IDs, and any structurally failed optional pass from the prior
handoff. Request the compact return defined in `dispatch.md`; inspect report
sections on demand. Both independent verdicts must still cover current HEAD.
Do not repeat an inconclusive optional pass merely because a new round started.

**Verifying a version bump actually shipped** — one call replaces the
`gh api .../git/refs/tags`/`gh run list`/`gh release view` sequence:
```bash
python3 "${PLUGIN_ROOT:-.}/scripts/release_verify.py" --json
```
Defaults to `plugin.json`'s version. Non-zero exit = NOT fully verified — read
which check failed before reporting shipped.

**Merge handoff requires the proof, not the assertion** — pass the verifiable
`@sec` record (the `## @sec review` comment URL), never a bare "APPROVED" from
memory. This repo's PR author can't self-approve, so `reviewDecision` is
always empty; that's expected, don't re-block on it. (origin: #105 · 2026-07-16)

<!-- rules:origin-required -->
- **Autonomously dispatch pre-merge gates immediately upon PR open.** As soon as
  a PR is opened (by @dev, @sre, @design, or yourself), you MUST immediately and
  proactively dispatch @sec and @rev to review it in the background. Do NOT stop
  or return control to the user to ask for permission to proceed with reviews;
  a task is only Done when merged and validated. (origin: #243 · 2026-09-08)
- **The pre-merge gate is `@sec` AND `@rev` AND bots-adjudicated-at-HEAD.**
  Hold the merge until both grep-verifiable markers (`## @sec review` and
  `## @rev review`) are on the PR, each ending in a verdict; a BLOCK from
  either blocks the merge. Both markers MUST come from a trusted author
  (`OWNER`, `MEMBER`, or `COLLABORATOR`). Missing/unknown/untrusted
  association fails closed. The field is `authorAssociation` in
  `gh pr view <N> --json comments` / `gh issue view <N> --json comments`, and
  `author_association` in the REST `gh api .../issues/<N>/comments` payload.
  Select verdicts with the reference filter below this list (marker prefix
  AND trusted association). **Trust boundary:** the marker plus the
  association check makes a verdict findable and filters out forgeries from
  non-collaborators; it does NOT authenticate which reviewer or dispatch
  wrote it. Any trusted account, and under a shared `gh` login every operator,
  reviewer and the human alike, can type a `## @sec review` comment.
  **`reviewed-sha:` MUST equal the PR's current HEAD at merge time**
  (`gh pr view <N> --json headRefOid`); a verdict at an older SHA does not
  count, so new commits need a delta re-review at HEAD. **`reviewer:` SHOULD
  be present:** when dispatching `@sec`/`@rev`, mint a
  `reviewer: <harness>/<dispatch-id>` value (e.g. `claude-code/sec-pr<N>-<n>`,
  unique per dispatch) and pass it in the dispatch prompt; the agent echoes
  it verbatim. A present value that doesn't match what you dispatched means
  the verdict is not counted (re-dispatch, or surface it to the user). It is
  SHOULD, not MUST, because verdicts posted before this rule carry no such
  line, and because it buys audit traceability, not authentication.
  Inline bot reviews (Cursor/Greptile-class) live in `pulls/{n}/comments`, not
  in `gh pr checks`, are not `@sec`/`@rev`, re-run on every push, and never
  notify the operator loop — "CI green" is not permission to advance while a
  bot finding sits unaddressed. Anchor adjudication to the current HEAD SHA to
  cut stale-comment noise:
  `gh api repos/<org>/<repo>/pulls/<N>/comments --paginate --jq '.[] | select(.commit_id=="<HEAD_SHA>")'`.
  Per thread: verify it's actually addressed → reply citing the fixing commit
  → resolve the thread; a won't-fix requires a stated justification before
  resolving. Do this at every push, not once at PR-open, since bots re-comment
  on new commits. (origin: #125 · 2026-07-16) (origin: #139 · 2026-07-20) (origin: #216 · 2026-10-03)
- **Re-read the operating-invariants card before tagging or merging.** Treat
  `invariants-card.md` as a checkpoint at that
  moment, not just a post-compaction reminder. (origin: #41 · 2026-07-21)
<!-- /rules:origin-required -->

**Reference verdict filter** (run as
`gh pr view <N> --json comments --jq "<filter>"`; `scripts/test_review_trust.sh`
extracts and tests the block between these markers, keep them intact):
<!-- review-trust-filter:begin -->
```jq
.comments[]
| select(((.body // "") | startswith("## @sec review") or startswith("## @rev review"))
    and (.authorAssociation == "OWNER" or .authorAssociation == "MEMBER" or .authorAssociation == "COLLABORATOR"))
```
<!-- review-trust-filter:end -->

**Terminal-state playbook: branch protection `REVIEW_REQUIRED`, no eligible
non-author approver.** A repo can require a review from someone other than the
PR author; if the only available reviewers are bots/the author, `gh pr merge`
sits at `REVIEW_REQUIRED` indefinitely and no further push changes that.
<!-- rules:origin-required -->
- **Detect this early, not at merge time.** Check `reviewDecision` /
  `mergeStateStatus` right after opening the PR (or right after dispatching
  `@sec`/`@rev`), not only when the merge attempt itself fails — a late
  discovery burns a review cycle for nothing. (origin: #133 · 2026-07-20)
- **Escalate with a named ask, never a vague "blocked."** Request a specific
  human reviewer (`gh pr edit <N> --add-reviewer <user>`) or state the exact
  action needed ("a human with write access must approve or merge this PR");
  set the board item to **Blocked** with that ask as the status reason, don't
  leave it "In Progress" pretending work continues. (origin: #133 · 2026-07-20)
- **`--admin` override policy.** `gh pr merge --admin` bypasses the review
  requirement and is reserved for an explicit, in-the-moment human
  authorization for this specific PR — never a standing default, never
  inferred from a prior unrelated approval. Record who authorized it and why
  in the merge/PR trail. (origin: #133 · 2026-07-20)
<!-- /rules:origin-required -->

