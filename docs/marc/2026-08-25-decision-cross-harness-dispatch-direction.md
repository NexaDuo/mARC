# Decision record: no nested cross-harness dispatch bridge — harnesses stay board-mediated peers

- **Type:** decision record
- **Date:** 2026-08-25
- **Attribution:** operator + user decision on
  [issue #202](https://github.com/NexaDuo/mARC/issues/202), based on the
  `@research` brief posted there
  ([comment](https://github.com/NexaDuo/mARC/issues/202#issuecomment-5411718487)).
- **Status:** accepted (no code change — this records a decision *not* to build)

## Context

mARC ships the same six-specialist team to three harnesses
(`harnesses/{claude-code,antigravity,copilot}/marc`) with identical agent
definitions and an identical `tech-lead/SKILL.md`. Only the dispatch verb
differs, because each harness exposes its own native mechanism:

| harness | dispatch mechanism | background / isolation |
| --- | --- | --- |
| Claude Code | Agent tool, `subagent_type: marc:*` | `run_in_background`, worktree isolation |
| Copilot | `task` tool, `agent_type: marc:*` | `mode: background` |
| Antigravity | `invoke_subagent` (`TypeName`/`Role`/`Prompt`/`Workspace`/`Model`), plus `define_subagent`, `send_message` | `Workspace: share` |

Each operator can only spawn subagents **inside its own harness**, so a session
is single-vendor end-to-end. The only cross-harness convergence point today is
the board: all three write to GitHub Project #2 via `scripts/board.py`.

The question raised was whether to build a **bridge** — one harness's
`@techlead` dispatching specialists into another harness — and, if so, in which
direction. Three options were put on the table:

1. **Antigravity as operator** → Claude Code as dispatched specialist.
2. **Claude Code as operator** → Antigravity as dispatched specialist.
3. **Peers, board-mediated** — no nesting; each harness runs its own operator,
   and the GitHub Project board is the only shared state.

Option 3 was added to the study deliberately, so that "build no bridge" competed
on equal footing rather than being the residual of a two-way comparison.

## Decision

**Build option 3 first: harnesses remain board-mediated peers. No nested
cross-harness dispatch bridge is built in either direction.**

Options 1 and 2 are not rejected forever, but neither is built now. Revisiting
requires a concrete workload that specifically needs one operator's judgment to
select *and supervise* the other vendor's execution — see "When to revisit".

## Rationale

**The two nesting directions are symmetric in capability, not asymmetric.** Both
CLIs are solid *callees*: non-interactive print mode, prompt from argv or stdin,
structured `json`/`stream-json` output, documented exit-code semantics, per-run
workspace scoping (`--add-dir`), and non-interactive tool-permission
constraints. This was verified against both vendors' headless documentation and
locally against the installed CLIs (Claude Code 2.1.245, Antigravity 1.1.19).
So the direction of nesting could not be settled on callee capability — both
work.

**What breaks is the caller side, equally in both directions.** Neither vendor
exposes, for the *other* vendor's process, the thing a supervising operator
needs: background-completion notification and token/cost observability of a
dispatched run. Each vendor solved "supervise a background agent" only inside
its own binary — Claude Code's `claude agents --json`, Antigravity's interactive
`/agents` panel and `/tasks`. A cross-vendor caller would have to reimplement
supervision as PID-polling plus stdout-parsing. Callee cost is recoverable only
by parsing the callee's own self-reported `total_cost_usd` out of its stdout,
which means trusting and parsing another vendor's output as data.

**The governance gate does not need a bridge.** mARC's pre-merge gate — the
grep-verifiable `## @sec review` and `## @rev review` markers — is enforced by
`@techlead` reading GitHub PR comments after the fact, not by any harness's
internal tool loop. It is vendor-agnostic by construction and already survives a
vendor boundary today. Nesting adds no governance benefit.

**Nesting adds a distinct governance risk.** mARC's bounded-dispatch rules
(tool-call budget, no-progress stop) are applied by an operator against a
subagent whose steps it can observe. An externally shelled-out process is opaque:
the operator cannot count its steps, cannot detect no-progress, and cannot apply
its own stop criteria. Option 3 avoids this entirely, because each harness's
operator applies bounded dispatch only to its own subagents.

**Existing precedent points the same way.** This repo's CI already shells out to
`agy` (`.github/workflows/ci.yml`, checksum-pinned unprivileged bootstrap, then
`agy plugin install`). That is a one-shot blocking subprocess call —
CLI-as-installer/validator, not CLI-as-supervised-specialist. It is precedent
for option 3's spirit, not for either nesting direction.

**Option 3's cost is coordination, not capability.** Its risk is two operators
picking up the same board item — a board-hygiene problem with known solutions,
not a CLI capability gap. That is a materially cheaper problem than
reimplementing cross-vendor process supervision.

## Consequences

- No bridge code is written, and no cross-vendor supervision protocol is
  invented or maintained.
- Each harness's operator continues to pay its own orchestration cost against
  its own vendor quota, exactly as today. The "whose quota pays for
  orchestration" question does not arise.
- The board (`scripts/board.py` → GitHub Project #2) and the PR/`gh` governance
  layer remain the only cross-harness contract. Both are already vendor-agnostic;
  keeping them so is now a load-bearing constraint, not an incidental property.
- A follow-up work item covers the one real cost of this choice: running two
  harnesses as peers against the same clone without conflicting.

## Alternatives considered

- **Option 1 — Antigravity as operator, Claude Code as specialist.** Rejected
  for now: requires reimplementing cross-vendor supervision, with no capability
  gain over option 3.
- **Option 2 — Claude Code as operator, Antigravity as specialist.** Rejected
  for now, on the same grounds. The evidence *very mildly* favours this
  direction if a bridge is ever built, only because Claude Code's background
  agent JSON schema is better documented than Antigravity's equivalent — a
  documentation-completeness observation, not a confirmed capability gap in
  Antigravity.
- **A2A / MCP as a ready-made bridge.** Not available today: neither vendor's
  CLI documentation ties `claude` or `agy` to A2A. Recorded as prior-art
  context only, at low confidence (see the brief's own caveats).

## When to revisit

Revisit if any of the following becomes true:

- A vendor ships a documented API for supervising an *external* agent process
  with completion notification and usage reporting.
- A concrete workload appears that genuinely needs one operator to select and
  supervise the other vendor's execution — not merely to run work on it.
- Option 3's coordination cost proves worse in practice than the estimated cost
  of building and maintaining a bridge.

## Evidence quality and known gaps

The brief's CLI-capability findings are moderate-high confidence: fetched live
from both vendors' current headless docs and corroborated against the locally
installed CLIs. The following gaps were flagged by `@research` and are recorded
here so the decision is not read as better-evidenced than it is:

- `code.claude.com/docs/en/sub-agents` returned 404 during the study; some
  subagent-completion detail may be missing.
- No full Antigravity CLI reference page was fetched — only `agy --help`, which
  prints a short synopsis. Undocumented flags may exist.
- No public precedent was found of anyone running these two CLIs in a nested
  operator/specialist relationship. That absence is itself a finding, but it is
  an absence-of-evidence claim over a fast-moving surface.
- The A2A/MCP prior-art paragraph rests on third-party coverage rather than
  fetched vendor primary sources, and is explicitly not load-bearing for this
  decision.

## Sources

- [issue #202](https://github.com/NexaDuo/mARC/issues/202) and its `@research`
  brief ([comment](https://github.com/NexaDuo/mARC/issues/202#issuecomment-5411718487))
- [Claude Code Docs: Headless mode](https://code.claude.com/docs/en/headless)
- [Claude Code Docs: Agent view](https://code.claude.com/docs/en/agent-view)
- [Antigravity Docs: CLI headless mode](https://antigravity.google/docs/cli/headless/)
- [Antigravity Docs: CLI subagents](https://antigravity.google/docs/cli/subagents)
- Local CLI probes: `claude --version` (2.1.245), `agy --version` (1.1.19),
  `claude --help`, `claude agents --help`, `agy --help`, `agy agents --help`
- This repo: `.github/workflows/ci.yml` (pinned unprivileged `agy` bootstrap),
  `harnesses/*/marc/skills/tech-lead/SKILL.md` (per-harness dispatch verbs and
  the `@sec`/`@rev` gate), `scripts/board.py` (GitHub Project #2 convergence)
- [issue #204](https://github.com/NexaDuo/mARC/issues/204) — the follow-up on
  running peer operators against one clone without conflicts
