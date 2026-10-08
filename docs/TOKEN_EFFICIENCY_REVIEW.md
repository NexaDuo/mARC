# Review and dispatch token efficiency

Date: 2026-10-08. Status: proposed changes, pending review and runtime evaluation.

This proposal compares a user-provided field review with mARC source at
`08a46276ddacaac825fd7e4a8904bdafef5a21a2`. It does not publish the originating
conversation, consumer identifiers, local paths, prompts, or reports. The field
observations have not been independently replayed. Source-level checks confirm
the instruction and wrapper behavior below, not a measured billing impact.

## Findings and disposition

| Finding | Source evidence | Disposition |
| --- | --- | --- |
| Repeated inconclusive optional reviews | Both reviewer profiles required an additional skill pass while warning that its target or output could be wrong. | Make the extra pass conditional on a specific question, verified target and compatible tools. Persist structural failure reasons across rounds; retry only after a relevant configuration change. |
| Long reports re-enter the operator context | Reviewer output required full findings without a compact handoff contract. The wrapper returns raw stdout and prompt-bearing command fields. | Request a summary of at most 300 words, retain full findings separately when possible, and select report sections on demand. Avoid printing whole wrapper results. |
| Fresh specialist sessions reconstruct prior work | `dispatch_agent.py` builds fresh invocations; repository policy also favors stateless dispatch. | Retain fresh sessions and pass compact continuation state. Do not add automatic resume without measuring its cache and context tradeoffs. |
| Nested additional review duplicates some work | Manual audits and extra review skills were both required. | Preserve independent security and correctness reviewers; condition only their extra skill pass. An unavailable or inconclusive extra pass cannot supply a PASS. |
| Dispatch reference reread for every call | The tech-lead entry point explicitly required it. | Read once per session/plugin version and invalidate after lost context or routing/configuration changes; keep a short per-dispatch checklist. |
| Incomplete usage observability | The wrapper records duration, command, stdout and stderr, but no normalized usage. Its subprocess timeout is not a token limit. | Document the limitation; defer an executor-specific telemetry adapter until its schema and coverage are verified. |
| Model choice conflated with token volume | Profiles configure a model, not a measured bill or a controlled comparison. | Leave model defaults unchanged. Evaluate cost separately from context volume and review quality. |

The field review also attributes unnecessary repetition and broad result reads
to operator choices. Prompt changes address those choices, but cannot guarantee
executor compliance or establish a percentage saving. Necessary review findings
and fixes are not counted as waste.

## Implemented scope

The source changes are in `core/skills/tech-lead/` and
`core/agents/{security,review}.md`; compiled copies are regenerated for all four
harnesses. The 2026-10-08 refinement replaces mandatory extra review passes from
rules #125/#191/#236 with conditional passes, retaining their provenance tags.
It also removes mandatory routine praise from correctness reports.

Compact handoffs retain reviewer identity, reviewed SHA, verdict, new finding
IDs, disposition of pending IDs, validation and limitations. They never authorize
dropping findings to meet a word limit. A report-only reviewer without a separate
artifact channel returns the summary and full findings; the operator persists
them. No file-writing privilege is added to a read-only reviewer.

A follow-up receives the target checkout and HEAD, prior reviewed SHA, delta
location, unresolved findings, report locations and failed optional-pass state.
The delta focuses attention but does not remove responsibility for interactions
with the full PR diff. Current-HEAD independent verdicts remain the merge gate.

## Follow-up experiments

1. Reproduce a nested skill targeting the wrong checkout. Verify a later round
   skips it until the target configuration is fixed. An unverifiable effective
   target must skip the optional pass, not the required manual audit.
2. Exercise report-only and permitted-comment modes with enough findings to
   exceed 300 words. Verify all findings remain available and no prohibited
   comment or file write occurs.
3. Compare matched review tasks before/after, including multiple rounds. Record
   review quality, optional-pass counts, operator input volume and execution
   time separately. Source compilation and structural tests cannot establish
   these runtime outcomes.
4. For a future telemetry adapter, capture executor/session identity and raw
   structured usage locally. Distinguish absent metrics from zero, cumulative
   from incremental counters, and cached from uncached input. Do not combine
   duplicate event streams or assume operator counters include external CLIs.
5. Compare fresh compact continuations with supported resume on equivalent tasks
   before changing dispatch defaults. Keep implementer and reviewer state
   independent. No billing or savings claim is justified by word counts alone.

Separating the historical dispatch rationale into an on-demand reference is a
possible later cleanup. This patch avoids rewriting routing, model policy,
executor flags or read-only enforcement alongside the targeted process fixes.

## Validation of this proposal

- Regenerated all harness outputs with `scripts/compile_prompts.py`.
- `scripts/test_codex_harness.py`: 6 tests passed, including package structure,
  version alignment, prompt size and read-only routing safeguards.
- `core/scripts/test_script_parity.py`: passed across all four harnesses.
- `core/scripts/check_rule_origin.py`: passed for 18 governed source files.
- `claude plugin validate harnesses/claude-code/marc`: passed with the existing
  `minimumVersion` unknown-field warning.
- `git diff --check`: passed.

These are structural checks. No billable specialist sessions or controlled
before/after task replay were run. The 300-word return and retry rules are prompt
instructions, not new runtime-enforced limits. Independent review and runtime
validation remain required before treating the proposal as a shipped fix.
