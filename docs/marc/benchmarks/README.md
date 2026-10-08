# docs/marc/benchmarks/

Raw token-benchmark measurements, committed so they survive the 90-day
retention on the workflow's `token-benchmark-raw-<run-id>` artifact.

Two kinds of subdirectory live here:

- **`<tag>/`** (e.g. `v26.9.15/`) — written by `scripts/run_token_benchmark.sh`
  on a real (paid) run. Since issue #309, the ONLY way to take that path is
  running the script locally with an explicit opt-in (see "Running it
  locally" below) — CI never takes this path on its own. This is the cache
  `run_token_benchmark.sh` reads back on the *next* release to skip
  re-running arm A (the previous release's baseline) when the task set,
  model, and CLI version haven't drifted (`task_hash` in `manifest.json`);
  `resolve_prev_release_tag()` walks back past any ancestor tag with no
  manifest here (a patch tag, or a release that was never measured) to find
  one. A run against a branch ref (e.g. running locally on `main` without
  first tagging) writes here too, keyed by that branch name (e.g. `main/`)
  rather than a tag — expect that directory to be overwritten by the next
  such run; it is a cache, not an archive.
- **`run-<workflow-run-id>/`** (e.g. `run-34987534132/`) — a specific,
  named workflow run preserved verbatim because it produced a result worth
  keeping independent of the tag/branch cache above (for example the first
  real three-arm causal-proof measurement, #294/#296). These are never
  overwritten by the benchmark script and are safe to treat as a permanent
  record.

Every directory holds the same file set: the per-task
`baseline-*.jsonl` / `post-*.jsonl` / `toggle_baseline-*.jsonl` /
`toggle_post-*.jsonl` (one line per `claude` invocation), `task_names.txt`,
and `manifest.json` (`model`, `tasks`, `iterations`, `task_hash`, `tag`,
`claude_version` — the identity that makes two runs' numbers comparable or
not). A real run also produces one `bulk_reader-bulk_read_forced.jsonl` (arm
D, see below) alongside those.

## The arms

- **A** — the previous release (`$PREV_TAG`, a separate git worktree), no
  `[token_guard]` configured. The baseline everything else is measured
  against.
- **B** — the current commit, `[token_guard] max_read_lines = 350`
  (`bulk_reader` unset/off). Enforcement only: an over-threshold untargeted
  `Read` is denied.
- **C** — the current commit, `max_read_lines = 999999` (i.e. structurally
  never fires). The same-commit, guard-off control used for the "Causal
  Proof" table and the noise floor.
- **D** (issue #324) — the current commit, `max_read_lines = 350` **plus**
  `bulk_reader = true`. Enforcement + execution: the same over-threshold
  read that arm B denies instead gets delegated to the bulk-reader worker
  (#320/#322) and summarized. Run against **one task only**
  (`bulk_read_forced`, the task verified to deterministically trip the
  guard's enforcement path) — arm D makes real, billed `claude` invocations
  through the worker delegation path, so it is not repeated across the full
  task set the way A/B/C are.

**The comparison that matters for the execution-layer claim is B vs D**, not
D vs C. Arm B already isolates "what does denying cost"; arm C already
isolates "what does no guard at all cost" (that pairing — enforcement-on vs
enforcement-off — is exactly the one that produced the known-negative
result in run 34987534132: guard costs 2.6x, noise floor 33.7%). Neither of
those tells you whether *delegating* instead of *denying* helps. B vs D
does, and only B vs D. `scripts/benchmark_report.py` prints this pairing
under its own "Execution Layer Proof" heading, separate from the
inter-release and causal-proof tables, and any result in it must still
clear the measured 33.7% noise floor (#298 is open on giving that floor a
dispersion measure) before it counts as an effect.

**Arm D never feeds the "Tokens Saved" badge.** Per #303/#305 the badge is
fed the shipped-default arm — `bulk_reader` defaults to **off**
(`docs/team.toml.example`), so arm D is not a configuration any release
actually ships with. `token-benchmark.yml`'s "Generate Dashboard Files" step
continues to read only `baseline-control.jsonl` / `toggle_baseline-control.jsonl`
(arm A / arm C) plus the arm B/C `neutral` pair for the noise floor; arm D's
files are never passed to it. Arm D's own numbers surface only in
`scripts/benchmark_report.py`'s console/job-summary output.

## Running it locally

Issue #309: CI never spends real API credit on its own. The paid,
three-task/three-arm measurement runs by hand, on the owner's own account,
at release time. **Use `LOCAL_RUN=true` for this** (issue #314).

### One-time setup: your own persistent config dir

The first real local run aborted at the preflight with `Not logged in ·
Please run /login`. `LOCAL_RUN=true` points `CLAUDE_CONFIG_DIR` at an
isolated directory so plugin/marketplace mutations never touch your real
Claude Code config — but a brand-new directory has no login in it either,
so the CLI starts logged out and every invocation fails. Do this once:

```
mkdir -p ~/marc-bench-config
chmod 700 ~/marc-bench-config
CLAUDE_CONFIG_DIR=~/marc-bench-config claude
# then run /login inside that session
```

Log in there separately from your normal `claude` session because this
directory is where the benchmark installs and removes plugins/marketplaces
under the hood, and that must never touch your real, everyday config.

**Never** symlink, copy, or otherwise redirect your real
`~/.claude/.credentials.json` into an isolated config dir as a shortcut
around this. It was tried: the CLI refreshes its OAuth token via
write-temp-then-rename, which replaces a symlinked/copied credentials file
with a new regular file inside the scratch dir, and deleting that scratch
dir afterwards destroys the only copy of the refreshed token — logging you
out of your real session. Always give the isolated dir its own real login
instead.

### Running the benchmark

```
LOCAL_RUN=true LOCAL_RUN_CONFIG_DIR=~/marc-bench-config GITHUB_EVENT_NAME=workflow_dispatch REAL_RUN_INPUT=true scripts/run_token_benchmark.sh
```

`LOCAL_RUN=true` is what makes this safe to run on your own machine instead
of a disposable CI runner:

- Skips `npm i -g @anthropic-ai/claude-code` — it never upgrades your global
  CLI mid-session. The version is still resolved from whatever `claude` is
  already on your `PATH` and still feeds the drift-guard hash (issue #281
  item 3 stays fixed).
- Points `CLAUDE_CONFIG_DIR` at an isolated directory for the whole run, so
  every `claude plugin marketplace add/remove` and `claude plugin install
  marc@nexaduo` call lands there instead of mutating your real Claude Code
  config or marketplace registrations. With `LOCAL_RUN_CONFIG_DIR` set (as
  above), that directory is the persistent, already-logged-in one you set up
  once — it is reused across runs and never deleted. Without it, the script
  falls back to a fresh throwaway directory for the run (its path is
  printed; inspect or delete it yourself when done) — but that fallback
  starts logged out, so the preflight below will fail unless you supply
  `LOCAL_RUN_CONFIG_DIR`.
- Checks out the arm-A (previous release) worktree in a fresh scratch
  location instead of `../arm-a`, so it never creates a sibling directory
  next to your checkout. That location is always a new `mktemp -d` path made
  fresh for every run regardless of `LOCAL_RUN_CONFIG_DIR` (it can never
  collide with a leftover from an earlier one); the worktree registration is
  removed via an `EXIT` trap (`git worktree remove --force`, then `git
  worktree prune`) when the script exits. The preflight telemetry state
  directory (below) is likewise always fresh scratch, never inside
  `LOCAL_RUN_CONFIG_DIR` — reusing it there would let a stale row from an
  earlier run produce a false pass.
- Before spending on the full 45-65 invocations (4 tasks x arms A/B/C +
  arm D's 1 task, issue #324), makes exactly ONE real
  invocation and verifies a telemetry row was actually written under the
  isolated config. If it wasn't, the run aborts immediately instead of
  burning the rest of the budget for zero samples — this guards against the
  Stop hook silently failing to fire under `CLAUDE_CONFIG_DIR` isolation, a
  different failure from run 35046956691 (which burned ~19 billed
  invocations against an exhausted `ANTHROPIC_API_KEY` credit balance, not a
  telemetry gap).

Do **not** set `ANTHROPIC_API_KEY`: the script never reads it (it's a CI-only
secret `token-benchmark.yml` injects to force API-key billing on a
disposable runner). Locally, with no key present, `claude` bills your own
Claude Code subscription — the ~45-65 invocations a full run makes are
enough to consume your 5-hour usage window. `GEMINI_API_KEY` isn't read
anywhere in this script either; skip it. Run with `git fetch --tags` already
done, ideally right after tagging a release so the walk in
`resolve_prev_release_tag()` finds the right baseline.

## Codex arm (issue #346)

A before/after measurement of the **Codex** harness, run by hand, never by
CI. Same script, same gate, selected with `BENCH_HARNESS=codex`. It runs
instead of the Claude arms, not alongside them.

What it measures: one fixed scenario in a throwaway fixture repo (a tiny
synthetic codebase with one seeded defect) on two refs. Arm A is the
previous release tag (`resolve_prev_release_tag()`, or `CODEX_BASE_REF`),
checked out as the usual scratch arm-A worktree. Arm B is the current
checkout. Each run starts a fresh `$marc:tech-lead` thread: engineer fix,
then `@sec` + `@rev`, one fix round, then a delta re-review. Arms are
interleaved (A1, B1, A2, B2, ...) so drift over time hits both.

Per run, `scripts/codex_rollout_tokens.py` writes one summary record:
operator input / cached / output from the rollout's per-request counters
(deduplicated by `response_id`, with `token_count` events as the fallback and
cross-check), request and turn counts, **poll turns** (requests that only
followed a sleep/wait/peek at a specialist, each of which re-sends the whole
carried context), compactions, and specialist tokens by role and round.
Claude Code specialists are mapped to their transcripts through a `claude`
shim that pins `--session-id`; Codex specialists are read from the isolated
`CODEX_HOME`. `benchmark_report.py --harness codex` prints the median of N per
metric for A and B, plus a MAD noise floor per arm; a headline delta inside
that floor is reported as noise, not an effect.

Isolation:

- Each arm gets its own `CODEX_HOME` under the scratch root, with the plugin
  installed from **that ref's** `harnesses/codex/marc` (via its
  `.agents/plugins/marketplace.json`). The Claude plugin in
  `LOCAL_RUN_CONFIG_DIR` is re-pointed at the same ref before every run.
- `auth.json` is **symlinked** from your Codex login (`~/.codex`, or
  `CODEX_AUTH_HOME`), never copied, read or printed. A copy would fork the
  OAuth refresh token, the same failure class as the Claude
  `.credentials.json` note above. If Codex ever replaces the symlink with a
  regular file (a token refresh), the run stops immediately and leaves the
  scratch directory in place. If `codex login status` then fails in your
  normal shell, log in again.
- The operator runs with `--sandbox danger-full-access` (override with
  `CODEX_SANDBOX`), because the Claude Code specialists it dispatches write
  to their own config dir and need the network. Commands run as your user,
  inside a fresh `mktemp` fixture.

Run it (needs the one-time `LOCAL_RUN_CONFIG_DIR` setup above and a logged-in
`codex`):

```
BENCH_HARNESS=codex LOCAL_RUN=true LOCAL_RUN_CONFIG_DIR=~/marc-bench-config GITHUB_EVENT_NAME=workflow_dispatch REAL_RUN_INPUT=true scripts/run_token_benchmark.sh
```

Cost envelope: `2 x CODEX_ITERATIONS + 1` operator threads (default N=3:
**7**, one of them a one-line preflight that aborts the run if login or
rollout parsing fails). Each scenario thread dispatches about 6 specialist
invocations (engineer x2, `@sec` x2, `@rev` x2), so about 36 specialist
runs on top. Codex usage is billed to your Codex login; the Claude Code
specialists bill the subscription logged into `LOCAL_RUN_CONFIG_DIR`. Budget
for a few million mostly-cached tokens per thread. Lower N with
`CODEX_ITERATIONS=2` for a cheaper, noisier read. Results land in the
working directory as `codex_baseline-scenario.jsonl` (A),
`codex_post-scenario.jsonl` (B), `codex_iterations.txt` and
`codex_manifest.json`, and are not committed automatically. They hold only
numbers, but review them before committing anything under `docs/marc/`,
which is public.

Every other combination (any push/PR, a dispatch without `real_run=true`,
`LOCAL_RUN` unset) takes the free stub path, which writes synthetic
`codex_*` files so CI exercises the report code at no cost
(`scripts/test_run_token_benchmark_codex.sh`).

## Claude arms: CI-only invocation (do not run this locally)

The same script also has a non-`LOCAL_RUN` code path used by
`token-benchmark.yml` on a disposable GitHub Actions runner:

```
GITHUB_EVENT_NAME=workflow_dispatch REAL_RUN_INPUT=true scripts/run_token_benchmark.sh
```

This form upgrades the **global** `claude` CLI in place, mutates whatever
Claude Code config is on the machine it runs on (removing and re-adding the
`nexaduo` marketplace entry, installing the plugin for real), and creates a
`../arm-a` worktree next to the checkout. That is fine on a runner that gets
torn down after the job, and unsafe on a developer's own machine — it can
mutate the configuration of the very Claude Code session invoking it. Use
the `LOCAL_RUN=true` form above for any run on your own machine.
