# Engineering director: operations first, minimal team contracts

- Date: 2026-11-23
- Status: proposed product change, implemented as instructions in this PR;
  live adapter capabilities are not yet proven.
- Tracking: [#411](https://github.com/NexaDuo/mARC/issues/411),
  [PR #413](https://github.com/NexaDuo/mARC/pull/413).
- Attribution: operator/user design, independent architecture, security and
  correctness review, followed by user-provided operational critique.

## Problem and revised decision

A contract for asynchronous deliveries alone does not operate a team of teams.
The director must recover sessions, find the responsible techlead, deliver to
idle sessions, supervise authorized monitoring and arbitrate shared resources.
The first case is multiple independent teams in one repository; cross-repository
federation is an extension, not a prerequisite.

Implement eng-director as a leadership skill in the coordination session. Teams
remain independently running techlead sessions. A private workspace may hold
personal context and infrastructure bindings; only minimal authorized task
context travels to a team. Role, skill and running session remain distinct.

The executable instructions are maintained once in
[the director skill](../../core/skills/eng-director/SKILL.md), with
[the operating contract](../../core/skills/eng-director/references/operating-contract.md)
and [operations/recovery](../../core/skills/eng-director/references/operations.md).
Compiled copies target each supported harness. This PR supplies no daemon,
message broker, native scheduler or universal terminal adapter.

## Changes after operational review

1. Replace the initial large state machine with an objective, acceptance evidence,
   owner, blocker and checkpoint. The operating contract contains a filled generic
   example. Revisions and event protocols can be added when demonstrated needs
   justify them; no distributed exactly-once guarantee is claimed.
2. Use action-scoped delegated authority. Shared credentials do not authenticate
   actors, but they also do not require asking the user again for every routine
   action already delegated. New authority and owner-only decisions must come
   through the established user channel, never inferred from comment formatting.
3. Compaction and verified resume of the same conversation preserve ownership.
   A process incarnation is not a claim identity. A replacement owner still needs
   withdrawal or human resolution under the existing claim protocol.
4. Permit scoped session operations and resource guardianship when delegated.
   Inspect adapters before use, verify targets and outcomes, preserve permission
   dialogs and escalate operations beyond the mandate.
5. Model aggregate resource budgets and a registry from logical team name to
   current session/contact. Use the repository's existing worktree and claim
   protocol for overlapping files and integration ownership.
6. Prove idle-session delivery first. Then validate monitoring health, cursor
   recovery, resume, resource pressure and evidence-based reports. Sample bounded
   acceptance targets live in the operations reference; all require recorded
   outcomes rather than successful tool invocation alone.
7. Bind to the actual board meanings and transition owners. Technical blocking
   need not mean waiting for the user, and Done follows repository release rules.

## Relationship to prior governance

The [2026-08-25 decision](2026-08-25-decision-cross-harness-dispatch-direction.md)
keeps board-mediated peers and rejects an unproven nested cross-harness bridge.
This proposal retains independent teams and their local dispatch/review gates.
It explicitly extends the coordination layer with optional user-delegated
operational supervision through inspected local adapters. It does not claim
that another vendor's native subagent supervision is available.

The [claim protocol](../../core/skills/tech-lead/references/concurrent-operators.md)
remains authoritative: assignee and board status are not ownership; no timer-based
reclaim or competing claim to take over a dead session is introduced. Unknown
continuity blocks recovery until resolved. The director's position grants no
exception to independent review, no self-merge or production permissions.

Default bans on unsolicited periodic triage and guessed context maintenance
remain. The director adds an explicit opt-in procedure for restoring an existing
user-authorized watcher/schedule and measured context policy. It checks that the
mandate is still applicable and that no duplicate instance exists. Consumer
thresholds, schedules and private bindings are never shipped as plugin defaults.

## Scope and verification

This skill can use approved existing local operations; it does not reproduce a
particular consumer's scripts or install their environment. With no adapter,
perform read-only discovery/status and report the specific capability gap.
No private workspace, paths, hardware inventory or application facts are included.

Structural validation and independent behavioral evaluation establish instruction
quality, not end-to-end runtime support. Record the real-repository pilot by
scenario with terminal evidence. Live idle wakeup, restart, recurring monitoring
and process mitigation stay unverified until actually exercised under the local
mandate. No release or installed-cache update is implied by this PR.
