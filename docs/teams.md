# The team and coordinating several teams

The full roster of handles, and how an engineering director coordinates several teams.

Back to the [README](../README.md).

## The roster

Think of your project as an IRC channel. **`@techlead`** holds channel-operator status. It listens to the discussion, compiles it into well-specified, tracked work on your GitHub Project board, and then pings the right specialist to do it.

| handle       | role                 | pings for                                            |
|--------------|----------------------|------------------------------------------------------|
| `@eng-director` | team coordination | dependencies, operational recovery, delegated resource limits |
| `@techlead`  | channel operator     | convene, spec, record on the board, dispatch, track  |
| `@dev`       | engineer             | app/service code, IaC, deploy scripts, schema, tests |
| `@sre`       | reliability          | deploys, observability, incidents, backups/DR, cost  |
| `@design`    | front-end            | UI screens and overall web flows                     |
| `@sec`       | security (read-only) | pre-merge diff review; one half of the mandatory merge gate |
| `@rev`       | review (read-only)   | pre-merge correctness review; the other half of the mandatory merge gate |
| `@research`  | researcher (read-only) | external evidence (such as benchmarks or documentation) as a cited brief |
| `@arch`      | architect (read-only) | one hard decision, escalated to on explicit triggers; advises in a `## marc:arch decision` comment |

`@techlead` and `@eng-director` are leadership **skills** you invoke in a
session. The specialists are subagents dispatched for individual tasks. Start
with `/marc:tech-lead` for project delivery; add a director when several teams
need coordination. Each techlead keeps ownership of its work and reviews.

## Coordinate several teams

Open a separate session in your coordination workspace and invoke:

```
/marc:eng-director
```

The workspace can be private and does not need to be a Git repository. Tell the
director which teams and repositories it coordinates, where their queues and
checkpoints live, and which routine operations you delegate. Several teams can
work in the same repository or across repositories and harnesses.

For example: “Coordinate the API and client teams. Read their queues and recent
owner decisions, identify dependencies, and report verified progress. Ask before
changing session or resource settings.”

The director reads the work queue before declaring a team idle, routes decisions
and dependencies, and checks evidence before reporting completion. With your
existing local tooling and authorization, it can reconcile watchers and
schedules, resume sessions from checkpoints, and manage shared resource limits.
Machine paths, session mappings, schedules and quotas stay in your private
workspace; the skill carries the reusable procedure.

Invoking the skill starts a leadership conversation. Session wake-up, restart,
context measurements and monitoring depend on the host tools and local adapters;
installing mARC does not create a background supervisor. Delegated operations
remain subject to their checks, permissions and the repository's review gates.

See the [director skill](../core/skills/eng-director/SKILL.md),
[operating contract](../core/skills/eng-director/references/operating-contract.md)
and [recovery procedures](../core/skills/eng-director/references/operations.md).
