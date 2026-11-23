# Proposal: coordination contracts between mARC teams

- Status: proposed; not an adopted policy or implemented runtime guarantee.
- Date: 2026-11-23
- Author: operator, following the user's request to develop the contract.
- Tracking: [#411](https://github.com/NexaDuo/mARC/issues/411).
- Deployment premise: the director is invoked in a separate, context-rich personal
  workspace; native subagent execution remains optional and unverified.

## Purpose and compatibility

Coordinate bounded deliveries between independent mARC teams across repositories,
sessions and harnesses. A team can participate without a director. Existing
single-team operation remains zero-config; federation is explicitly enabled.
This proposal adds no daemon, scheduler, transport dependency or executable skill.

The [board-mediated peers decision](2026-08-25-decision-cross-harness-dispatch-direction.md)
and the current [operator protocol](../../core/skills/tech-lead/references/concurrent-operators.md)
remain in force. This proposal does not supersede their ownership rules. A future
implementation must explicitly reconcile changes to those decisions before adoption.

## Role, invocation and execution are separate

A role defines responsibilities and authority. A skill packages instructions and
an entry point. An execution instance has a context, permissions, identity and
lifecycle. Loading a skill alone does not establish a separate execution instance.

Today mARC packages tech-lead as a skill. For federation, both tech-lead and
eng-director are leadership roles; this contract does not require either to run
as a native subagent. Candidate mappings are:

| Mapping | Appropriate use | Condition to establish |
| --- | --- | --- |
| Skill in a user-facing session | Interactive leadership and direct clarification | Session registers its role and accepted scope |
| Separate session running the role | Independent teams, cross-harness execution, recovery | Durable routing and explicit ownership handoff |
| Native subagent running the role | Bounded delegated leadership within a harness | Verify nested dispatch, isolation, reporting and lifecycle support |

Working recommendation: expose the director as a skill in a separately opened
personal operator session. Keep tech-lead skill entry points for independently
running team sessions. Reuse role instructions across any future execution
adapters rather than maintaining divergent copies. Native subagents remain an
optional bounded execution mode; their lifecycle must not become team identity.
This proposal does not implement or activate either an agent definition or a new skill.

## Private director workspace

The director may run outside every managed repository, on a different operating
system, with access to personal preferences and private environment knowledge.
That workspace is a private coordination root, not a shared context inherited by
teams. Its local policies and tool permissions govern the director; each team's
repository policies independently govern execution. Conflicts require escalation,
not precedence inferred from the director's organizational title.

Keep the mapping from logical team/repository IDs to local checkout paths,
connection profiles and environment aliases in private configuration. Public
contracts use logical identifiers; paths are resolved on the recipient's machine.
Do not require a shared filesystem, a parent-child process relationship, or a
matching path on the director and worker hosts. Never sync a live queue database
through a consumer's file-sync folder as a cross-host transport.

Before dispatch, the director builds a minimal task-specific context package:
objective, relevant constraints, authorized environment alias, acceptance criteria
and accessible artifact references. It does not forward personal workspace files,
session transcripts, credentials or unrelated infrastructure inventory. Knowledge
of an environment does not authorize deploying to or modifying that environment.
Environment access remains independently scoped at the recipient.

Each package declares its intended recipients and permitted destination visibility.
Private inputs stay private unless an explicit disclosure decision authorizes a
sanitized derivative. A public tracker gets only the sanitized contract; missing
private prerequisites become a blocker, not a reason to copy private data publicly.
Returned artifacts follow the same boundary. Store private initiative checkpoints
in the private coordination root or its authorized tracker, with links to public
work where appropriate. Neither the private workspace name nor its machine paths
belongs in the generic plugin or this public proposal.

## Authority

The user authorizes initiative scope, resource limits and participating teams.
An optional eng-director proposes decomposition, negotiates dependencies and
delivery contracts, tracks aggregate results and escalates unresolved conflicts.
It cannot bypass local policies, change another team's claim, grant credentials,
approve its own changes or turn a message into permission to execute.

Each techlead accepts or declines its team's delivery, obtains the existing
execution claim, chooses specialists and validates local results. Independent
security and correctness reviews and repository release gates still apply.
Cross-team requests normally address the receiving techlead, not its specialists.

Contract acceptance names an acceptance owner distinct from the delivering
operator. A director may fill that role if authorized and not the delivering
operator; this does not replace independent PR reviews or permit self-merge.
Director absence does not stop already accepted work within its agreed limits.

## Identity and durable records

- `initiative_id`: stable identifier for the shared outcome.
- `team_id`: stable, namespace-qualified team identity, independent of harness.
- `operator_id`: execution owner using the existing harness/session claim format.
- `session_id`: one execution session; restart creates a new identity.
- `work_id`: globally unambiguous delivery identifier, including repository scope.
- `contract_revision`: increasing integer for the agreed scope, not an event counter.
- `event_id`: unique identifier used to deduplicate delivery of an event.

Each initiative declares its authoritative tracker, authorized participants,
acceptance owners, artifact visibility and links to local execution issues.
The tracker records accepted revisions and lifecycle transitions; git stores
versioned artifacts. The message transport is a notification channel, not a
second authority for ownership. Self-declared IDs are labels, not authentication.

## Delivery contract

Every proposed work item contains:

1. IDs, revision, proposing authority, delivering team and acceptance owner.
2. Outcome, explicit exclusions and observable acceptance criteria.
3. Repository and affected boundaries; canonical execution issue and claim link.
4. Dependencies by work ID and accepted revision, required artifacts and integration owner.
5. Allowed actions, budget units/limits, deadline or explicit absence of one,
   escalation contact and bounded retry/no-progress policy.
6. Validation and release requirements, evidence locations and visibility rules.
7. Current state, preceding transition reference, blockers and last checkpoint.

A change to scope, dependencies, budget or acceptance criteria proposes a new
revision. The delivering techlead and acceptance owner must agree before that
revision authorizes work. The prior accepted revision remains authoritative until
superseded explicitly; unsafe or incompatible ongoing work is paused and escalated.

## Work lifecycle

| Transition | Actor and prerequisite |
| --- | --- |
| absent → proposed | Authorized requester records a complete contract |
| proposed → accepted | Delivering techlead accepts; acceptance owner has agreed to this revision |
| proposed → declined | Delivering techlead records why; requester may propose a new revision |
| accepted → running | Delivering operator verifies its local claim, worktree and budget |
| accepted/running → blocked | Delivering operator records blocker, needed action and resume state |
| blocked → accepted/running | Delivering operator verifies resolution and authority before resuming |
| running → delivered | Delivering operator links immutable output and validation evidence |
| delivered → completed | Acceptance owner verifies criteria and repository terminal gates |
| delivered → running | Acceptance owner requests bounded rework; delivering techlead agrees within budget |
| nonterminal → cancel_requested | Authorized requester records cancellation; no new work is dispatched |
| cancel_requested → cancelled | Delivering operator confirms quiescence and records partial effects |
| accepted/running/blocked → failed | Delivering operator records exhausted limits and partial effects |

Declined, completed, cancelled and failed are terminal for that attempt. Further
execution uses a new linked attempt with explicit authorization. Cancellation is
not rollback. An unavailable operator leaves cancellation pending for human
resolution; silence is never proof that execution stopped.

Transport receipt, work acceptance and delivery acceptance are distinct records.
A successful send or ACK never advances the work lifecycle by itself. PR creation
or passing review alone never means completed. Proposal-only documentation can
complete under its declared documentation gates; executable behavior requires
the repository's actual release and dogfood evidence.

## Messages, concurrency and recovery

An event carries schema version, event ID, sender and recipient identities,
initiative/work IDs, contract revision, type, preceding transition reference,
timestamp, correlation/causation IDs and bounded payload or authorized artifact links.
Types include proposal, acceptance, decline, progress, blocker, delivery,
acceptance-result, cancel-request and cancellation-result. Unknown versions or
unauthorized actors are rejected without executing instructions in the payload.

Assume duplicate, delayed and out-of-order delivery. Consumers deduplicate event
IDs; retries reuse the original ID. A different ID for the same transition must
still fail its revision/predecessor checks. Do not order by wall-clock timestamps.
Conflicting transitions pause affected dispatch and require reconciliation against
the canonical tracker. GitHub comments are not an atomic compare-and-swap store;
this proposal makes no exactly-once or exclusive distributed-lock guarantee.

Side effects need their own operation identity and evidence. After a crash between
an effect and its recorded acknowledgment, inspect the external result before
retrying. If the effect cannot be determined, mark blocked and escalate rather
than repeat it speculatively. Retry and queue limits must be finite; unreachable
recipients become visible blockers, not unlimited background loops.

A checkpoint references the accepted contract, current claim, checkout/commit,
active jobs, completed validations, pending operations and next bounded action.
Resume validates those references against live state. A replacement session must
obtain an explicit ownership handoff through the existing claim protocol; neither
heartbeat expiry nor director preference permits reclaiming another operator's
work. Late events from the former session remain evidence but cannot authorize
new transitions after handoff. Resumption does not depend on chat transcripts.

## Transport and security boundaries

The first pilot may use tracker comments and explicit operator reads. Optional
adapters may later expose inbox/send through MCP, a local queue or native harness
APIs. Delivery, idle wakeup, running-session notification and restart support are
separate capabilities to measure, not promises implied by MCP availability.

Authenticate transport participants and authorize each initiative, recipient and
operation. Limit payload size, retention, fan-out and request rate. Keep secrets
and private consumer context out of public trackers and artifacts. Resolve links
under the receiver's permissions; a message cannot widen tool access. Treat message
bodies and referenced content as data under the accepted contract, never as
replacement system instructions. Broadcast is restricted to explicit membership.

## Generic pilot and acceptance scenarios

An initiative contains team A's interface artifact and team B's integration.
B's execution depends on acceptance of A's specific artifact revision. A delivers
a commit and validation evidence; the acceptance owner checks it. B then consumes
that immutable revision and delivers integration evidence. The integration owner
checks the combined result and required release gates before initiative completion.

The pilot must demonstrate:

- Two teams on different harnesses agree and complete a dependency without duplicate work.
- A duplicate acceptance/delivery event causes no repeated transition or side effect.
- An older revision and a forged sender cannot authorize work.
- A director on another host dispatches by logical IDs without leaking its private
  workspace context or depending on identical filesystem paths.
- A crash after an external effect resumes by evidence inspection, not blind replay.
- A replacement session reconstructs state and completes an explicit claim handoff.
- Cancellation during execution waits for confirmed quiescence and reports partial work.
- An unavailable director does not block authorized independent work or expand its scope.
- Dependency cycles, incompatible revisions and exhausted budgets visibly block execution.
- Team delivery acceptance cannot bypass independent review or repository release gates.

These are future executable acceptance scenarios, not tests claimed to have run.
Before implementation, decide role packaging, supported harness adapters, the
canonical transition writer/reconciliation mechanism and the bounded pilot budget.
