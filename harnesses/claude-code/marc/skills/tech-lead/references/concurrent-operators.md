#### Concurrent operators (claim before you dispatch)
Two `@techlead` operators — different harnesses, or two sessions — may run
against the same clone with no supervisor between them. These rules are the
whole coordination protocol; there is no locking layer, by design.
<!-- rules:origin-required -->
- **Claim with a comment marker, not the assignee field.** Post a comment whose
  body starts with the fixed string `## @techlead claim` and carries at least
  `operator: <harness>/<session-id>`, `issue: #<N>`, and an ISO-8601
  `claimed-at:` timestamp — the same grep-verifiable-marker discipline already
  used for `## @sec review` / `## @rev review`. Verify with
  `gh issue view <N> --json comments` (or a scoped `gh api …/comments` grep for
  `^## @techlead claim`), never with assignees. **An issue with no valid
  `## @techlead claim` comment is not claimed, regardless of who or what is
  assigned to it** — this is what makes ordinary human triage safe again.
  (origin: #213 · 2026-08-25)
- **A claim comment counts only from a trusted author — this repo is public,
  posting a comment needs no collaborator status.** Before treating a
  `## @techlead claim` marker as valid, check the comment author's
  association. **The field name depends on which command you use — they
  genuinely differ, verify against a real issue rather than trust this from
  memory:** `gh issue view <N> --json comments --jq
  '.comments[].authorAssociation'` (camelCase — this is the primary,
  documented path, the same command already used to grep the marker itself)
  or, on the raw REST path, `gh api repos/<owner>/<repo>/issues/<N>/comments
  --jq '.[].author_association'` (snake_case). A marker counts as a claim only
  if that value is `OWNER`, `MEMBER`, or `COLLABORATOR`. Any other association
  (`NONE`, `CONTRIBUTOR`, `FIRST_TIME_CONTRIBUTOR`, etc.), or an association
  field that comes back empty because the wrong field name was queried for the
  command used, is not a valid claim — ignore it as noise, the same as an
  issue with no marker at all. **State the trust boundary honestly: this
  marker coordinates cooperating operators, it is not an authorization
  mechanism.** A malicious or compromised collaborator account can still forge
  another operator's token string; the association check plus the
  human-escalation rule below are the mitigation, not cryptographic identity —
  don't overclaim what this protects against. **This same association check —
  by field name, per command, as specified here — applies to every marker in
  this protocol that changes claim state, not just this one:** a claim and its
  withdrawal are two sides of one state transition and are trusted identically
  (see the withdrawal rule below). (origin: #213 · 2026-08-25)
- **Supersedes #208's "claim with the assignee field" wording — the assignee
  cannot carry operator identity.** #208 shipped `gh issue edit <N>
  --add-assignee @me` as the claim, on the premise that "only the assignee
  carries operator identity." That premise fails under a shared `gh` token: two
  harnesses (or two sessions of the same harness) on one machine authenticate
  as the *same* GitHub login, so both operators assign, both re-read
  `[the-same-login]`, and both conclude "I am alone" — the race #208 knowingly
  accepted turns out to be undetectable, not just racy. Worse, a human
  self-assigning an issue during ordinary triage becomes indistinguishable from
  an operator claim, so every pre-existing self-assignment now reads as a
  possible squat under the stale-claim rule below. The assignee field is
  demoted to a **human-visible signal only** (who a person thinks owns
  something) and MUST NOT be read as, or treated as evidence of, operator
  identity — replaced by the comment marker above, which encodes harness and
  session and so distinguishes two sessions of the same harness too.
  (origin: #213 · 2026-08-25)
- **The claim is racy, knowingly — and only association-checked claims
  participate in the tie-break.** Posting the claim comment is read-check-act
  with no compare-and-swap, so simultaneous claims can interleave. This is
  **accepted, not deferred**: GitHub's GraphQL exposes no optimistic-concurrency
  field on `UpdateIssueInput` or the comment-creation mutations, so there is
  nothing to adopt and closing the window would mean building an external lock.
  After posting, re-read the issue's comments and drop any marker that fails
  the author-association check above before comparing anything — a forged
  marker never enters the tie-break at all. If more than one *valid* (live,
  association-checked) `## @techlead claim` marker remains for the same issue,
  break the tie deterministically over the **`operator:` token**, not the
  login: the **case-insensitively lowest `operator:` value keeps the item**
  (e.g. `antigravity/sess-7` beats `claude-code/sess-2`). The tie-break moved
  off the login specifically because a shared `gh` token yields one login for
  every operator on the machine — the login cannot distinguish them,
  `operator:` always can.
  **Autonomous withdrawal is permitted only when losing to a claim that PASSED
  the author-association check.** In that case, and only that case, the losing
  operator posts a `## @techlead withdraw` comment (see below) and re-picks.
  Any other outcome — the competing claim fails the association check, its
  `operator:` value is malformed, or it otherwise looks chosen to win the sort
  (e.g. `0/0`, empty, non-`<harness>/<session-id>`-shaped) — is a **suspected
  forged claim**: do not withdraw, surface it to the user, and leave the item
  pending their decision. A forged marker must never be able to make a
  legitimate operator stand down by itself. Never "both drop" — a mutual drop
  stalls an item nobody then owns. The loser of a legitimate tie is not starved
  of work, but it does lose *every* contested claim to a lower-sorting peer;
  accepted, as rotation isn't worth machinery at two operators.
  (origin: #205 · 2026-08-25) (origin: #213 · 2026-08-25)
- **Withdrawal has its own fixed marker, is association-checked exactly like
  a claim, and must come from the same `operator:` it retires.** Post a
  comment whose body starts with the fixed string `## @techlead withdraw` and
  carries the same `operator:` and `issue: #<N>` fields as the claim it
  withdraws. **A `## @techlead withdraw` comment only retires a claim if it
  passes the same author-association check as a claim (`OWNER`/`MEMBER`/
  `COLLABORATOR`, field name per command as specified above) AND its
  `operator:` value matches the claim it targets exactly.** A withdrawal that
  fails either test is not a withdrawal — ignore it as noise, and if it looks
  deliberate (a plausible `operator:` value, posted shortly after a real
  claim, from a failing or absent association), treat it the same as a
  suspected forged claim: surface it to the user, do not treat the original
  claim as retired. This closes the asymmetry a claim-only check leaves open —
  an `operator:` token is plainly visible in a public claim comment (that is
  the point, it is grep-verifiable), so without this check any untrusted
  account could copy it into a forged withdrawal and make a live claim read as
  abandoned, reopening the exact forgery the claim-side check exists to close.
  **A claim is live only if the thread has no *valid* (association-checked,
  matching-`operator:`) `## @techlead withdraw` comment posted after it** —
  that is the one resolution rule for "is this issue claimed," so a
  withdrawal that doesn't delete the original claim can never be mistaken for
  a live one by the same grep, and a forged withdrawal can never retire a
  claim it didn't post. Deleting the original `## @techlead claim` comment is
  allowed as a courtesy but is never required and never assumed — always
  resolve by the marker pair, not by the comment's presence or absence.
  (origin: #213 · 2026-08-25)
- **Read `git worktree list` before every dispatch that will mutate files.** It
  is the one coordination signal both operators genuinely share without a
  shared identity or a board round-trip: one `.git` registers every operator's
  checkout, cross-harness, and git itself refuses a second checkout of a branch
  already checked out elsewhere. Run `git worktree list --porcelain` and treat
  a target branch that already appears there as **being worked on by another
  operator** — do not re-cut that branch, do not force a second checkout; go
  claim a different item or ask the user. (origin: #214 · 2026-08-25)
- **A worktree that is `locked`, or whose directory is gone, sitting at the
  base branch's SHA with zero commits and no linked PR, is a dead worktree —
  distinct from both a live claim and #206's squat case below.** A live claim
  has commits or an open PR behind it; a squat is a marked claim that a human
  must adjudicate. A dead worktree is neither: it is leftover registration from
  a dispatch that never produced work. Diagnose with `git worktree list
  --porcelain` (state) plus the linked-PR check already used for stale claims.
  The remedy is concrete and never autonomous — **surface it to the user and
  let them choose**, because a worktree can hold uncommitted work the operator
  cannot safely judge as disposable: `git worktree prune` if the directory is
  already gone, `git worktree remove --force` if it is `locked` but the
  directory still exists. Only after the worktree is cleared does #206's
  writer-isolation rule apply again to that branch. (origin: #214 · 2026-08-25)
- **Stale claims are reclaimed by a human, never by a timer — and this
  escalation applies only to a valid, live marked claim.** An issue carrying an
  association-checked `## @techlead claim` comment with no *valid, matching*
  `## @techlead withdraw` and no linked PR is *not* self-evidently abandoned —
  TTL reapers misfire on slow-but-alive workers. Surface it and ask; don't
  auto-steal. Where you do not control the peer operator, a claim that never
  clears is a **squat**: escalate to the user rather than racing it or
  reclaiming unilaterally. A claim that failed the author-association check is
  a suspected forgery, not a squat — surface it as such (see above), don't run
  the squat playbook on it. An issue with only a human assignee and no claim
  comment is ordinary triage, not a squat — see the "not claimed" rule above.
  (origin: #206 · 2026-08-25) (origin: #213 · 2026-08-25)
- **Isolation extends to the operators themselves**, not just to the
  specialists they dispatch: an operator that will mutate files takes its own
  worktree (see Principles), and two of them never share a branch or working
  tree. An in-repo worktree directory (e.g. `.claude/worktrees/`) belongs in
  the **committed** `.gitignore`, never only in the local, unversioned
  `.git/info/exclude` — the latter doesn't survive a fresh clone and protects
  nobody but the machine that wrote it. (origin: #206 · 2026-08-25) (origin: #214 · 2026-08-25)
<!-- /rules:origin-required -->

