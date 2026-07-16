# Changelog

All notable changes to mARC are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.16.3] - 2026-07-16

### Added
- **`board_reconcile.py` operator script (#103).** Bundled a one-call, provider-agnostic board reconciliation script (`scripts/board_reconcile.py`) that reads repo facts from `team.toml` at runtime (zero-dependency, no hardcoded org/repo/board), normalizes them into a digest (open items' id/title/status/assignee/linked PR, recent merges, release state incl. plugin manifest ↔ tag/release match, local ↔ remote `main` drift), and degrades gracefully when the `project` scope or board isn't configured. Ships a `BoardProvider` interface with a concrete `GitHubProvider`, so a future Azure DevOps / Jira provider can plug in against the same normalized contract. Added optional `[board].provider` to `docs/team.toml.example` (defaults to `github`). The `@techlead` skill now runs this script once instead of hand-rolling `gh issue list`/`gh pr list`/`gh release view`/`git fetch` reconciliation snippets, and the documented `token_sentinel.py` invocation now resolves via the plugin root regardless of the operator's cwd.

## [0.16.2] - 2026-07-15

### Fixed
- **Token-guard cache-read reweight.** The automatic context-size cost guard now weights discounted prompt-cache reads at roughly 0.1x and labels whether a turn is cache-read-dominated vs generation-dominated, so it stops firing false positives on normal large-context sessions (#100).

## [0.16.1] - 2026-07-15

Harness setup alignment and documentation fixes.

### Changed
- **Aligned Google Antigravity installation instructions.** Updated documentation to use the official `bash <(curl ...)` command and canonical plugin repository syntax instead of local paths.
- **Factored `/compact` prompt nudges in `@techlead`.** Moved the `/compact` nudge and task-boundary context-hygiene advisory into harness-specific placeholders to avoid suggesting `/compact` when running on Google Antigravity.
- **Cleaned up `COMPATIBILITY.md`.** Removed pre-existing absolute local path URLs from `harnesses/antigravity/marc/COMPATIBILITY.md`.

## [0.16.0] - 2026-07-15

Dual-harness template compilation (#80): mARC's prompts now compile from a
single `core/` source into each supported harness, and Google Antigravity
joins Claude Code as a second supported harness. Both harnesses ship at this
same 0.16.0 version, and going forward they version in lockstep.

### Added
- **`core/` as the single source of truth for agent prompts.** Prompt templates
  for `@techlead` and the specialist bench now live once under `core/`, with
  `{{ placeholder }}` tokens for anything that differs per harness (config
  directory, project/plugin-root environment variables, the subagent dispatch
  mechanism, `plugin.json` path, and so on).
- **`scripts/compile_prompts.py`.** Reads each harness's `compile.json` and
  compiles the `core/` templates into that harness's `marc/` tree, substituting
  its placeholders. Both harnesses compile from the same source, so a prompt
  change made once in `core/` lands correctly in each.
- **Google Antigravity harness support.** A new `harnesses/antigravity/marc/`
  tree with its own `plugin.json` and `compile.json`, compiled from the same
  `core/` templates as the Claude Code plugin, using Antigravity's own
  environment variables and dispatch conventions (`invoke_subagent` instead of
  the Agent tool).
- **CI: compile-drift check.** `ci.yml` now re-runs `compile_prompts.py` and
  fails the build if the compiled output in either harness differs from what's
  committed, so a `core/` template edit can never ship out of sync with the
  harnesses that consume it.
- **CI: upgrade-path check.** `ci.yml` installs the plugin as it exists on
  `origin/main`, then upgrades it to the current PR's version, and fails if the
  upgrade doesn't complete cleanly.
- **CI: harness version-parity gate.** `ci.yml` now compares the `.version`
  field of the Claude Code and Google Antigravity manifests and fails the
  build if they ever drift apart, keeping the two harnesses' releases in
  lockstep going forward.

## [0.15.2] - 2026-07-14

Task-boundary context hygiene and a third token-guard band (#81), closing a gap
where cost blowups happen at a moderate tool-call count carrying an oversized
re-read context, below the runaway-loop band.

### Added
- **`@techlead`: task-boundary context-hygiene advisory.** When a discussed work
  item is closed out and the session has actually grown, `@techlead` now says so
  plainly and recommends `/compact` or a fresh session before the next item. The
  skill text explains why this is advisory rather than automatic: `/compact`
  cannot be triggered programmatically (the harness only compacts on the user's
  manual `/compact` or its own near-limit auto-compaction; hooks are reactive and
  can only block, never initiate).
- **`@techlead`: delegate-execution hard rule.** New governed Principle (origin
  `#81`, inside the origin-required fence): heavy execution (commands, tests, PR
  mechanics) belongs on a specialist subagent, not on the operator's own main
  thread, so the operator's context stays lean.
- **Token-guard third band: context-size / per-turn-token.** `token_sentinel.py`
  and `hooks/token-guard.sh` gained a warn-only guard on tokens processed in the
  current turn, independent of model tier or call count
  (`MARC_TOKEN_GUARD_TOKENS_THRESHOLD`, default 150000). It catches a
  moderate-call-count turn that still drags in an oversized context, same
  band-debounce shape as the existing call-count guard, still warn-only and
  always exits 0.

## [0.15.1] - 2026-07-13

Trigger-gated cross-version compatibility Principle added to the `@dev` and `@sre`
agent rule-sets (generalizes the "supersede, don't delete" Principle from #68).

### Added
- **`@dev` + `@sre`: cross-version state compatibility (release-versioned
  artifacts).** New trigger-gated Principle (identical in `agents/engineer.md` and
  `agents/sre.md`, origin `#78`): when a change introduces/alters shared on-disk
  state not namespaced by version, OR migrates an artifact multiple installed
  versions read (config, memory, caches, tmp state), treat old and new versions as
  concurrent — version the state path or add a `schema_version`-aware reader, keep
  shared-artifact migrations additive and reversible (supersede, never destructively
  rewrite/delete), and keep hook entrypoints pinned via `${CLAUDE_PLUGIN_ROOT}`
  rather than a `latest` symlink. Outside that trigger, no cross-version ceremony.

## [0.15.0] - 2026-07-13

Rule-origin governance (#68): durable rules now carry their provenance. Every
agent Non-negotiable, every tech-lead Principle, and the dispatch
cost-discipline rules are fenced with `<!-- rules:origin-required -->` markers
and tagged `(origin: #NN · YYYY-MM-DD)`, so a later reader can trace any rule to
the issue/PR that justified it. A CI gate enforces the tags and forbids silent
deletion (supersede a rule, do not drop it).

### Added
- **Rule-origin CI gate** (`scripts/check_rule_origin.py`, wired into
  `ci.yml`): a stdlib-only scanner that fails the PR if any rule inside a
  `rules:origin-required` fence lacks an `(origin: #NN · date)` tag, or if a
  fence is left unclosed. The CI step runs it on the real files (positive) and
  on a synthetic copy with one tag stripped (negative), so the gate proves on
  every run that it catches the regression it exists for.
- **Recording-discipline rules** in the tech-lead skill's step 3 "Record": tag
  every governed rule with its origin, and sanitize before recording on a public
  tracker (keep a consumer's private-repo internals in a private team note; the
  public issue/board carries only tool-generic, sanitized findings — the
  `#65`→`#66` incident).
- **Supersede-don't-delete Principle**: removing an origin-tagged rule now
  requires explicit justification in the removing PR.

### Changed
- Backfilled `(origin: …)` tags across the five agent rule-sets and all
  tech-lead Principles; closed the previously-unclosed dispatch
  `rules:origin-required` fence introduced with the cost guardrails.

## [0.14.0] - 2026-07-12

Mid-session model-switch guard (#73): a third, distinct cost guard alongside the
runaway-loop guards of #69/#71. Switching the model mid-session invalidates the
prompt cache — the prefix cached under model A cannot be reused by model B, so
B's next call is a full cache-write of the whole context instead of a cheap
cache-read, and flip-flopping repeats that cost.

### Added
- **Warn-only mid-session model-switch detection** in the shared
  `token_sentinel.py --hook` logic. It flags a genuine MAIN-thread A->B model
  change that carries the cache-invalidation fingerprint (a spike in
  `cache_creation_input_tokens` with `cache_read_input_tokens` collapsing — the
  inverse of steady state), and emits the SAME non-blocking channels as #71
  (`hookSpecificOutput.additionalContext` + `systemMessage`, no `decision`,
  always exit 0). The advisory names the switch (A->B) and the ~NK-token context
  that was re-cached, and suggests escalating at a natural context break or
  running `/compact` first. Debounced to once per genuine switch event (keyed by
  turn + from/to) so repeated tool calls in the same turn stay silent; a later
  flip re-arms. Re-cache write floor tunable via `MARC_MODEL_SWITCH_MIN_CACHE_WRITE`
  (default 20000). If both the runaway (#71) and switch (#73) guards fire on one
  tool call, their advisories merge into a single non-blocking payload.
- **False-positive trap handled.** With #69 model tiering, specialist subagents
  run on Sonnet while the operator runs Opus. The detector compares models ONLY
  within the main session's linear turn sequence and ignores any transcript entry
  marked `isSidechain: true` (a subagent/sidechain runs in a separate context and
  cache), so a dispatch never fires a false switch warning. The first model in a
  session is never treated as a switch.
- **Self-test coverage** (`scripts/test_token_sentinel.py`, CI Tier 1): a
  main-thread A->B switch with the cache-write spike warns exactly once;
  steady-state same-model turns never warn; a subagent/sidechain on a different
  model never warns (the false-positive trap); the initial model is never a
  switch; a switch without the cache-write spike stays silent; the hook always
  exits 0.

### Changed
- **Tech-lead skill: note the model-switch guard.** An origin-tagged rule records
  that the operator should escalate to Opus at a natural context break (or
  `/compact` first) and never flip-flop models mid-session, since each switch
  re-writes the whole cache.

## [0.13.0] - 2026-07-12

Automatic runaway-loop guard (#71): the manual token sentinel from #69 becomes
preventive so users who never run anything manually are still protected.

### Added
- **Warn-only `PostToolUse` token-guard hook.** A new non-blocking hook
  (`harnesses/claude-code/marc/hooks/token-guard.sh` → `token_sentinel.py --hook`)
  watches each session live: within the current user turn it counts consecutive
  assistant tool-call requests and the model in use, and when the model is
  Opus-tier and the count crosses a configurable threshold
  (`MARC_TOKEN_GUARD_THRESHOLD`, default 25) it emits a non-blocking advisory
  nudging `/compact` or a drop to Sonnet. The advisory rides Claude Code's
  `hookSpecificOutput.additionalContext` (for the model) plus a top-level
  `systemMessage` (for the operator); it sets no `decision`, never exits non-zero,
  and always exits 0 — a false positive costs one line of text, nothing more.
  Debounced to at most once per threshold band per turn via a tiny per-session
  temp-file state, so it never spams. Wired into `hooks/hooks.json` as a
  `PostToolUse` block matching the existing `${CLAUDE_PLUGIN_ROOT}` script call.
- **Shared counting logic + self-test.** `token_sentinel.py` now exposes an
  importable per-turn counting implementation used by BOTH the manual CLI
  (unchanged behaviour) and the new hook (DRY). A stdlib-only self-test
  (`scripts/test_token_sentinel.py`, wired into CI Tier 1) synthesizes fake
  transcript fixtures and asserts the advisory fires once past the Opus
  threshold, is debounced within a band and re-arms at 2N, never fires below
  threshold or for Sonnet-only turns, is non-blocking, and always exits 0.

### Changed
- **Tech-lead skill: note the automatic guard.** An origin-tagged rule records
  that the warn-only guard complements the manual sentinel, so the operator need
  not run anything for baseline runaway-loop protection.

## [0.12.0] - 2026-07-10

Dispatch-time token-budget guardrails (#69): three low-cost levers to bound the
worst-case token spend of background specialist loops.

### Changed
- **Specialist agents pinned to `model: sonnet`.** All five specialists (`@dev`,
  `@sre`, `@design`, `@sec`, `@research`) were running on the operator's default
  tier (often the most expensive), which multiplied cost across long autonomous
  tool-loops with fat re-read context. They now pin `sonnet` in their frontmatter;
  the operator keeps an explicit Opus escape hatch for a specific bounded item when
  the reasoning genuinely needs it. The `@techlead` operator model is unchanged.
- **Tech-lead skill §4: bounded-dispatch conventions.** New origin-tagged rules
  add the model-tier default plus Opus-override, a bounded-dispatch rule (never
  issue an open-ended `continue`; every dispatch carries stop criteria and an
  informal tool-call budget), and a reference-don't-embed rule (pass file/image
  paths in dispatch prompts, never pasted blobs).

### Added
- **Token-throughput sentinel script** (`scripts/token_sentinel.py`): an offline,
  zero-cost operator self-check that reads a Claude Code session `.jsonl` and
  reports per user turn the model, tool-call count, and tokens processed
  (input + cache_read + cache_creation), flagging runaway turns against
  configurable call/token thresholds. Referenced from the tech-lead skill.

## [0.11.2] - 2026-07-09

Process-lessons flush (precedent: PR #47, #58): the release-tag operator lesson
lands in the versioned tech-lead skill prose.

### Changed
- **Tech-lead skill: a version bump is not released until its tag is pushed and
  the release workflow ran green.** New Principle codifies that a merged
  manifest+CHANGELOG bump does not publish a release (the workflow is
  tag-triggered), that tagging the merge commit and watching the release workflow
  to green is part of "Done", and that release tags must be pushed one-per-push
  (GitHub fires no workflow when more than three tags arrive in a single push).

## [0.11.1] - 2026-07-06

Process-lessons flush (precedent: PR #47): three operator-buffer lessons land in
the versioned skill/agent prose.

### Changed
- **Tech-lead skill: a new convention must sweep its own declaring file.** When
  a flush lands a new rule, grep the file being edited (and sibling templates)
  for pre-existing violations, and prefer pairing the rule with an enforcing CI
  gate in the same PR.
- **Tech-lead skill: worktree isolation is enforced at dispatch time.** The
  operator passes worktree isolation on every mutating dispatch whenever more
  than one may be in flight, instead of relying on specialists to self-recover
  from shared-checkout collisions.
- **Human writing style for team-authored prose.** The tech-lead dispatch
  prompt now carries a writing-style instruction, and all five specialist
  agents get matching guidance: user-facing and GitHub-bound prose (briefs,
  issue/PR bodies, comments, docs) reads naturally, without machine-writing
  tells (em-dashes, formulaic triads, uniform bold-lead bullet scaffolding,
  hedge-then-assert filler).

## [0.11.0] - 2026-07-06

The per-repo team binding moves from flat `key=value` `.claude/team.config` to
**TOML** at `.claude/team.toml` (#51 — decided on the issue; decision record in
`docs/marc/2026-07-06-decision-team-config-toml.md`). **Breaking change**,
accepted while the project is early: the old file is no longer parsed by any
component — re-run `/marc:init` (or convert by hand from
`docs/team.toml.example`) to migrate.

### Changed
- **Binding format → TOML** (#51): native syntax highlighting (VS Code +
  GitHub), typed values, native arrays for path lists
  (`app_paths = ["src/", "services/"]`), and legal inline comments. Schema
  discipline: every key name stays **unique across the whole file** so the
  plugin's shell snippets keep extracting values with zero dependencies (no
  `yq`/TOML CLI on consumer machines) via key-anchored `sed`.
- **All parse/reference sites swept**: SessionStart hook, tech-lead discovery
  block (new `toml_get` sed helper), `/marc:init` template (now emits TOML,
  carries legacy values over and offers to delete the obsolete file), all five
  specialist agent definitions, README/CONTRIBUTING/ARCHITECTURE/landing page.
- **`docs/team.config.example` → `docs/team.toml.example`** (fully commented;
  the `workspace_dir` containment rule carries over verbatim; remaining bare
  team handles in comments backticked per the #47/#48 rule).
- **Tier 1 CI — "team.toml schema contract"** replaces the key=value gate:
  validates the example with `tomllib`, requires `gh_org`/`gh_repo`, enforces
  file-wide key-name uniqueness, and proves the documented zero-dependency sed
  extraction agrees with a real TOML parser (plus negative fixtures).

### Deprecated
- **`.claude/team.config`** (legacy): detected loudly, not parsed — the hook
  and the tech-lead skill print a one-line migration notice pointing at
  `/marc:init`.

## [0.10.0] - 2026-07-06

Durable team artifacts get a home + a file-write policy (PEF, #46): specialist
deliverables worth keeping (`@research` briefs, `@sec` reports, decision
records) are materialized by the operator into an in-repo, PR-gated workspace —
in THIS repo, `docs/marc/`, which is **public by construction** (GitHub Pages).
Read-only agents stay comment-only; no write carve-outs.

### Added
- **`docs/marc/` team-artifacts workspace** (#46): `README.md` documenting the
  folder's purpose, the PUBLIC-exposure warning, what belongs there vs what
  stays in issue comments, the `YYYY-MM-DD-<type>-<slug>.md` naming convention
  (type ∈ `brief|secreport|decision`), and the operator-materialized, PR-gated
  landing process. First inhabitant: the #46 `@research` brief itself
  (`2026-07-06-brief-team-artifacts-file-write-policy.md`).
- **Tech-lead skill step 7 — "Materialize durable specialist artifacts"**: the
  operator copies a persist-worthy `@sec`/`@research` issue comment into the
  repo's workspace and lands it via a reviewed PR; read-only agents never get
  write access. The workspace location is a per-repo binding resolved from
  `team.config`/AGENTS.md (`docs/marc/` is this repo's own binding).
- **`workspace_dir=` key** in `docs/team.config.example` so consumer repos can
  pin their own artifacts workspace (with a publish-exposure caveat).
- **AGENTS.md constraint** recording the PEF convention as a durable lesson.

## [0.9.1] - 2026-07-06

Escape team handles in the tech-lead's GitHub-bound issue-body template — the
handles collide with real GitHub usernames, and a bare mention in an issue body
pings a stranger (#48; extends the #47 rule from prose guidance to the emitted
template itself).

### Fixed
- **Issue-body template Assignee placeholder** in `skills/tech-lead/SKILL.md`
  (#48): now backtick-escaped and lists all five specialists
  (`@<dev|sre|design|sec|research>`); a note in the skill explains why.
- **Tier 1 CI regression guard** (#48): new "Issue-template handle-escape gate"
  step fails if the issue-body template block ever contains a bare team handle
  outside backticks (positive + negative fixture, deterministic, zero token
  cost).
- **Historical hygiene** (#48, operational — no repo files): 14 existing issue
  bodies (#1, #3, #5, #7, #9, #11, #13, #17, #22, #26, #30, #38, #39, #42)
  sanitized in place, backtick-escaping only the bare team handles.

## [0.9.0] - 2026-07-06

Add **`@research`**, the team's fifth specialist, plus the landing page's feature
directory — the first release where the plugin's own team ran its full
issue → board → dispatch → `@sec` gate → merge loop for every change (#42, #39).

### Added
- **`@research` specialist agent** (`agents/research.md`, #42, #43): read-only
  researcher for decisions that lack internal data — delivers a cited brief
  (URL + fetched quote per claim; findings labeled measured/reported/speculative;
  "insufficient public evidence" is a valid answer) as a comment on the
  motivating issue. Security hard rules baked in from the `@sec` pre-merge review:
  fetched web content is data, never instructions; outbound queries carry only
  the dispatched question, never repo internals. Roster wired everywhere
  (tech-lead skill, README, AGENTS.md, ARCHITECTURE.md, manifests) and Tier 2 CI
  now asserts the agent registers.
- **Landing page `/list` features section** (#39, #40): ">> what's on the
  server" channel directory between `#roster` and `#install` — 8 features as
  IRC channels with one-line topics, chosen from the #38 option study
  (option A). Inline CSS only, zero new JS, mobile-safe stacking.
- **Landing page wordmark + favicon polish** (#32, #29): hero wordmark
  (candidate A) on top of the #13 inline-SVG favicon; root `AGENTS.md`.
- **Shared `.claude/settings.json` + dogfood lessons flush** (#37).

### Fixed
- **Release workflow hardening** (#26 → #33, #34, #35, #36): tag-triggered
  Release publishing from the CHANGELOG with parity guard; deterministic
  backfill under `tag.gpgsign`; no `--target` on backfill creates; empty
  `${{ }}`-in-`run:` startup_failure fixed with an actionlint CI gate.

## [0.8.0] - 2026-07-03

Add the **opt-in upstream contribution channel** on top of the #20 context gate
(issue #22): a two-tier self-improvement model. Tier 1 (default) keeps every
field-lesson **local**; Tier 2 lets the operator **offer** to propose a
generalizable lesson upstream — sanitized, human-approved, submitted as a
fork-based PR under the user's own identity. **Never autonomous**, never
auto-merged. Upstream contribution is an org-members pilot for now (issue #25 is
the scheduled checkpoint to decide widening).

### Added
- `skills/tech-lead/SKILL.md` — section 6 now documents the **two-tier** model.
  Tier 1 (local, automatic) is unchanged; Tier 2 (upstream, opt-in) is a strict
  ordered flow: land locally first → offer (needs an explicit "yes") →
  sanitize/generalize (send the lesson, not the raw context) → **show the human
  the exact diff + PR body for approval** → open a fork-based PR against the
  plugin's upstream repo (resolved at runtime, no hardcoded slug) under the user's
  `gh` identity, labelled `field-lesson`. States the org-members pilot scope and
  references issue #25. Prose stays generic (anti-anchoring intact).
- `CONTRIBUTING.md` — how to contribute a field-lesson: opt-in, sanitized,
  fork-based, pilot = org members, zero auto-merge, CI + `@sec` + human-maintainer
  review required, high bar for skill/agent changes (injection surface).
- `.github/PULL_REQUEST_TEMPLATE/field-lesson.md` — field-lesson PR template with
  a sanitization checklist.
- `field-lesson` GitHub label.

### Changed
- `.github/workflows/execution-eval.yml` — clarifying comment: Tier 3 (headless,
  secret-bearing) is `workflow_dispatch`-only and therefore **never** runs on
  `pull_request` from forks, so fork PRs (incl. field-lesson contributions) can
  never reach `ANTHROPIC_API_KEY`. Tier 1/2 CI stays no-secret. No gate weakened.

## [0.7.0] - 2026-07-03

Gate the tech-lead's self-improvement behavior by context (issue #20). The
"capture process improvements into versioned source / buffer-flush" flow now
distinguishes the plugin's own source repo (dogfooding) from an end-user's
consuming repo, closing a privacy/ownership gap where the operator could edit the
plugin or open autonomous upstream PRs from someone else's repo.

### Changed
- `skills/tech-lead/SKILL.md` — section 6 now **gates process-improvement capture
  by context**, detected generically at runtime (the working tree contains this
  plugin's own `harnesses/claude-code/marc/.claude-plugin/plugin.json` with
  `name: marc`; no org/repo slug hardcoded). In the plugin source repo, plugin
  self-edits + upstream PRs remain allowed. In **any other (consumer) repo** the
  operator **MUST NOT** edit the plugin's skill/agent files or open an autonomous
  upstream pull request; improvements land only in the consuming repo's
  `AGENTS.md` / `.claude/team.config` / personal memory buffer, and genuinely
  upstream-worthy lessons are deferred to the sanctioned opt-in upstream channel
  (issue #22). Flush cadence updated to target the correct destination per context.

### Added
- `ci.yml` — deterministic Tier-1 "Self-improvement context-gating gate":
  asserts the SKILL.md carries the context-detection probe and the explicit
  prohibition on autonomous upstream PRs from a consumer repo, with a built-in
  negative test that fails if the guard prose is removed. Zero-cost, no live model.

## [0.6.0] - 2026-07-03

Dogfood refinements to the opt-in onboarding flow (`/marc:init` + the tech-lead
first-run offer, both shipped in 0.3.0). Hardens project-board discovery against a
real incident, tightens the generated `team.config` schema, cuts permission-prompt
noise during discovery, and extends the CI schema contract.

### Changed
- `skills/init/SKILL.md` + `skills/tech-lead/SKILL.md` — **project discovery no
  longer silently binds to a default/"untitled" board.** A dogfood run picked
  `project_number=1` (the owner's auto-created "untitled project"), routing issues
  to the wrong board. Both skills now: use a clearly-titled single match only after
  surfacing which board was chosen; and when the only match is generic/untitled or
  there are multiple matches, **ask the user** (AskUserQuestion) or leave
  `project_number` an explicit `TODO` with a written warning — never a silent guess.
  The tech-lead flow no longer auto-picks `.projects[0]`.
- `skills/init/SKILL.md` — the generated `team.config` template now emits
  **comments on their own line only**; no inline comments on `key=value` lines
  (the SessionStart parser is a naïve `key=value` reader). Documented the value-
  hygiene rule. Discovery probes are batched into a single Bash block to reduce
  permission prompts; the Write confirmations remain the intentional safety gate.
- `docs/team.config.example` — header documents the "comments on their own line
  only" rule; `project_number` guidance warns against the auto-created untitled
  project (verified: the example carries no inline-comment value lines).
- `.github/workflows/ci.yml` — the "team.config schema contract" step now also
  asserts **no inline comment on any value line** and that `gh_org`/`gh_repo` are
  present, and runs a **negative test** (an inline-comment fixture MUST fail the
  gate). Deterministic, zero token cost, no live model run.
- `.claude-plugin/plugin.json` — version `0.5.0` → `0.6.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

## [0.5.0] - 2026-07-03

A SessionStart safety-net hook now warns — one line, into context — when the
**installed** plugin version is behind the version on the repo's `main`, so users
with marketplace auto-update OFF don't silently miss fixes. Auto-update remains the
primary recommendation; the hook is the backstop.

### Added
- `hooks/outdated-check.sh` + a second `SessionStart` entry in `hooks/hooks.json`
  (coexists with the existing team.config-injection hook). It reads the installed
  version from `${CLAUDE_PLUGIN_ROOT}/.claude-plugin/plugin.json`, fetches the
  remote version from `plugin.json` on `main` (raw GitHub, NOT GitHub Releases),
  and if the installed major/minor is behind prints one nudge line with the update
  command. **Warn-only:** explicit short `timeout` on the network call; offline /
  error / rate-limit / missing tool (`jq`/`curl`/`wget`) => silent no-op — every
  code path exits 0 and never blocks or fails the session. Anti-nag: only nudges on
  a major/minor difference, not on patch bumps.
- `README.md` — Update section now leads with enabling marketplace auto-update for
  `nexaduo` (eliminates drift); the hook is documented as the safety net.

### Changed
- `.claude-plugin/plugin.json` — version `0.4.0` → `0.5.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

## [0.4.0] - 2026-07-03

The tech-lead operator now dispatches specialists **in the background** by default,
so the main conversation never freezes waiting on a slow subagent — the channel
stays responsive and multiple items run concurrently.

### Changed
- `skills/tech-lead/SKILL.md` — Dispatch (step 4) rewritten to instruct background
  dispatch by default (`run_in_background: true` on every Agent call); the operator
  is notified on completion and can resume/continue a running agent by id. Clarifies
  that "don't wait for confirmation" (don't pause for the user's "go") is **not**
  "block on the subagent" (sit synchronously until it returns). Independent items
  still fan out in parallel; **dependent** work stays sequenced but via background +
  the notification/track loop rather than synchronous blocking. `run_in_background:
  false` is reserved for a genuine strict dependency whose result is needed before
  anything else in the same turn — and even then background is preferred. Track-to-
  done (step 5) updated to note the operator stays responsive and is re-invoked when
  each background agent finishes.
- `.claude-plugin/plugin.json` — version `0.3.0` → `0.4.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

## [0.3.0] - 2026-07-03

Team-operation rules flushed from session learnings into the versioned plugin —
two generic, repo-agnostic guardrails for sequenced PRs and stale-base security
reviews.

### Changed
- `skills/tech-lead/SKILL.md` — two rules added. **Dispatch (step 4):** when
  dispatching PRs in sequence, instruct each specialist to branch from
  freshly-fetched `origin/main` (`git fetch origin && git checkout -b <branch>
  origin/main`), because merging a prior PR via `gh pr merge` does not advance the
  local `main`; a stale PR is re-synced with `gh pr update-branch <N>`, not
  re-cut. **Principles (verification):** before acting on a security reviewer's
  "MERGE BLOCKED", confirm the finding against GitHub's authoritative three-dot PR
  diff — a stale local base can misattribute a prior merged PR's changes to the PR
  under review, and the fix is `gh pr update-branch`, never deleting the flagged
  (already-merged) code.
- `agents/security.md` — before reviewing, `git fetch origin` and confirm the
  branch base is fresh (`git merge-base --is-ancestor origin/main HEAD`), then
  review via the three-dot PR diff so a prior merged PR's changes on a stale base
  aren't misattributed to the PR under review.
- `.claude-plugin/plugin.json` — version `0.2.0` → `0.3.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

[0.4.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.4.0
[0.3.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.3.0

## [0.2.0] - 2026-07-03

Opt-in onboarding — a repo can now graduate from ephemeral session-memory to a
persistent, versioned team binding, without ever writing a file silently and
without changing the zero-config default.

### Added
- `skills/init/SKILL.md` — the `/marc:init` onboarding skill. Discovers the
  repo's org/repo/project **at runtime via `gh`** and prefills three
  **independently opt-in** artifacts, each shown verbatim and written only on an
  explicit "yes": `.claude/team.config` (prefilled from the
  `docs/team.config.example` schema, unknowns left as clearly-marked `TODO`
  placeholders), an optional lean `AGENTS.md` **skeleton of section headings
  only** (no placebo prose, per the anti-anchoring lesson), and an optional
  `enabledPlugins` pin **merged** into `.claude/settings.json` (the deliberate
  "adopt for good" step — merge, never clobber). Nothing is ever written
  silently.

### Changed
- `skills/tech-lead/SKILL.md` — first-run offer: when **both** `AGENTS.md` and
  `.claude/team.config` are absent, `@techlead` offers to run `/marc:init`
  (explaining that session memory is ephemeral while `team.config` stabilizes
  board/paths across sessions) and proceeds only on confirmation. Zero-config
  behavior is byte-for-byte unchanged if declined.
- `.claude-plugin/plugin.json` — version `0.1.0` → `0.2.0` (`minimumVersion`
  unchanged — that is the min Claude Code runtime, a different field).

[0.2.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.2.0

## [0.1.0] - 2026-07-03

Initial release — the agent team extracted from a single repo into a portable,
cross-repo Claude Code plugin + self-marketplace, wrapped in an IRC/vaporwave
brand layer.

### Added
- `.claude-plugin/plugin.json` — plugin manifest (`marc`, v0.1.0, MIT).
- `.claude-plugin/marketplace.json` — self-marketplace entry pointing at the
  GitHub repo `NexaDuo/mARC` (the repo doubles as its own marketplace).
- `skills/tech-lead/SKILL.md` — `@techlead` channel-operator skill (`/tech-lead`)
  with **runtime discovery** of the target repo and Project board (via
  `.claude/team.config`, then `gh repo view` / `gh project list`) instead of
  hardcoded repo/project values.
- `agents/{engineer,sre,design,security}.md` — the `@dev`, `@sre`, `@design`,
  `@sec` specialist subagents, fully genericized (no stack-specific facts) and
  taught to read the consuming repo's `AGENTS.md` + `.claude/team.config` at
  runtime.
- IRC `@handle` identities across the roster (`@techlead`/`@dev`/`@sre`/`@design`/
  `@sec`) and a vaporwave ASCII-art console brand in the README, banner, and
  installer.
- `hooks/hooks.json` — a `SessionStart` hook that injects
  `$CLAUDE_PROJECT_DIR/.claude/team.config` into context (warns, never fails, if
  absent).
- `install.sh` — a safe, auditable installer (adds the marketplace + installs the
  plugin, prints the banner; no `curl | sh` of remote code).
- `README.md`, `LICENSE` (MIT), `.gitignore`, `docs/team.config.example`.
- Forward-compatible, multi-harness-ready layout: the Claude Code plugin is
  nested under `harnesses/claude-code/marc/` (manifest, `skills/`, `agents/`,
  `hooks/`), while the root `.claude-plugin/marketplace.json` (marketplace
  `nexaduo`) lists it via a full `source` path. Leaders live as `skills/`,
  specialists as a shared flat `agents/` pool, and future harnesses get their
  own `harnesses/<harness>/` sibling. Documented in `docs/ARCHITECTURE.md`.

[0.9.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.9.0
[0.8.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.8.0
[0.7.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.7.0
[0.6.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.6.0
[0.5.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.5.0
[0.1.0]: https://github.com/NexaDuo/mARC/releases/tag/v0.1.0
