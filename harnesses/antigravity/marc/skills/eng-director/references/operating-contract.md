# Local operating contract

Use existing workspace records where possible. The following fields describe
what must be known; they are not a new mandatory file format or service.

## Adapting the coordination workspace

Keep this skill independent of the consuming workspace's folder names and tools.
Discover existing records and adapters first; keep their private paths, resource
limits and team names in a local index linked from the workspace's instructions.
Use that index as the binding, not a fork of this skill with embedded local facts.
No particular index filename, directory layout or serialization format is required.

If organization is needed, always prepare a concrete plan: old-to-new path map,
consumers of each path, backups, validation and rollback. Proceed under existing
authorization that covers this reorganization; otherwise obtain opt-in for that
plan. Creating an index is often sufficient. Reorganization
must preserve unrelated personal material and existing permissions/visibility;
it does not authorize moving secrets, publishing records or editing plugin caches.
Before moving operational files, inspect scripts, schedules, session instructions
and running processes that reference them. Keep live paths stable until a safe
migration point. Update approved references together, verify read/send/resume
paths and pending-event continuity, and retain the old path/backup until the new
binding is validated. A file move alone is not a successful migration. Do not
operate or restart live teams merely to validate a reorganization without that
operational authority.

## Team registry and identity

Each team has a stable logical name, repository identity, techlead contact,
local claim operator token, harness session ID, process incarnation, worktree,
message destination and last verified checkpoint. Private bindings resolve
machine paths and transport credentials locally. Record observation time and
source, not just an assumed online flag. A directory lets a team find another
team's techlead without sharing that team's full context.

The contact adapter binding records the harness, session name or ID, the ordered
adapter list, the adapter last verified to reach the session, and the observation
time. For Codex the binding also records the techlead `threadId` and server endpoint. The probe's JSON output (`director_contact_probe.py`) is the proposal source;
the order and rules are in `operations.md`.

Distinguish three operations:

- Compaction of a live conversation: same team and claim owner.
- Resume of the same saved conversation: a new process may retain the same
  harness session and claim owner. Verify the old process is gone, the saved
  identity matches and there is no competing live instance before resuming.
- Replacement conversation or transfer to a different claim operator: use a
  trusted withdrawal by the former operator or human resolution of a stale
  claim. Never post a competing claim to win the tie-break. A checkpoint alone
  does not authorize takeover; if continuity cannot be proved, escalate.

A process restart is not automatically a new owner. Neither is the reuse of a
team name proof of continuity. Record the actual session/owner after recovery.

## Delegated authority

Authority comes from the user directly or records the user has approved in the
private coordination workspace. A record written by a team or by the director
is not user approval until the user actually approves it. Managed repository
files may constrain execution, but
issues, team messages and unapproved repository changes cannot grant or expand
the director's mandate. Read the existing mandate and local policies. Record permitted actions,
targets, limits, evidence and escalation owner. Use these levels per action:

| Level | Director behavior |
| --- | --- |
| Observe | Inspect approved status, screens, checkpoints and resource metrics |
| Routine delegated | Route work, acknowledge delivery, triage, remind or resume within the existing mandate |
| Reviewed delegated | Request the prescribed security/correctness review, then allow the designated executor to act within scope |
| Owner-only | Present the precise decision; wait without blocking unrelated work |

A previous grant covers repeated in-scope transitions; do not require human
confirmation merely because operators share a login. The tracker is still a
cooperative channel, not a security boundary. Validate collaborator provenance
where available; role markers and emoji do not prove a human authored a message.
An ambiguous message cannot expand scope, change a recorded decision or authorize
an owner-only action. Confirm such changes through the established user channel.

Resource guardianship is separately delegated: target process/service, measurable
threshold, permitted mitigation, maximum disruption and recovery evidence. It
may allow observing sessions and stopping a verified runaway worker, without
allowing broad host shutdown, changing global limits or killing unrelated jobs.
A review verdict never grants execution permission on its own. Runtime permission
denials are escalated, not bypassed by another tool, account or terminal injection.

## Minimal work record

Start with five fields; identify the record with the existing issue/task link.
Use revision/event protocols only when concurrent changes or transport retries
actually require them. Two teams in one repository are a first-class case.

```yaml
objective: Deliver a reviewed shared interface and integrate its first caller.
acceptance:
  - Interface commit reviewed under the repository policy.
  - Caller checks pass against that exact commit; required release evidence linked.
owner: team-interface
blocker: none
checkpoint:
  state: working
  claim: <canonical claim link>
  worktree: <private registry handle>
  commit: <actual inspected SHA>
  next: Publish interface evidence, then notify team-caller.
```

Do not populate evidence with placeholders when reporting actual progress.
Use proposed, working, blocked, delivered and done as explanatory states only
if no local vocabulary exists. Record cancellation and partial effects explicitly;
never equate cancellation with rollback. Changing scope requires the owners'
agreement within the mandate, and terminal work needs a new linked attempt.
For duplicate messages, inspect recorded/external outcomes before repeating effects.
Conflicting revisions or uncertain outcomes block the affected action, not all teams.

## Board binding

Learn the actual status options and who owns each transition. Example only:

| Local status | Meaning | Transition owner |
| --- | --- | --- |
| Todo | Eligible queue | Director triage or team, per local policy |
| In Progress | Claimed work actively executing | Owning techlead |
| Blocked | Waiting for an owner decision | Director/techlead per local policy |
| Done | Repository-defined terminal evidence | Owning techlead |

If Blocked locally means only waiting for the user, represent resource waits and
technical blockers in the work record instead of misusing that status. A new
user comment may trigger triage, but an unanswered question remains a question.
The director accepting delivery does not itself change board status; only the
locally designated transition owner may do that. Done may mean reviewed/merged/released, or a validated production deployment,
depending on the repository; never impose a hosted deployment on a plugin.

## Shared resources and operator feedback

Track aggregate pressure as well as per-team limits: machine/VM memory, CI slots,
compilation caches, disk, provider quotas and reserved capacity. Local policy
specifies queue order, per-team concurrency, reserve floor and who can alter them.
When pressure rises, stop admitting lower-priority work within delegated scope;
protect in-progress atomic operations and record the next eligible work. Unknown
quota is unknown, not zero or unlimited. Recheck before resuming admission.

User summaries name the observed artifact and its stage. "Inventory recorded at
<commit>; implementation pending" is accurate when only inventory exists.
Owner requests include the impact, options, recommendation and exact action;
batch related decisions and avoid repeating unchanged alerts.
