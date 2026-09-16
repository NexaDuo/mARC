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
not).

## Running it locally

Issue #309: CI never spends real API credit on its own. The paid,
three-task/three-arm measurement runs by hand, on the owner's own account,
at release time. **Use `LOCAL_RUN=true` for this** (issue #314):

```
LOCAL_RUN=true GITHUB_EVENT_NAME=workflow_dispatch REAL_RUN_INPUT=true scripts/run_token_benchmark.sh
```

`LOCAL_RUN=true` is what makes this safe to run on your own machine instead
of a disposable CI runner:

- Skips `npm i -g @anthropic-ai/claude-code` — it never upgrades your global
  CLI mid-session. The version is still resolved from whatever `claude` is
  already on your `PATH` and still feeds the drift-guard hash (issue #281
  item 3 stays fixed).
- Points `CLAUDE_CONFIG_DIR` at a fresh throwaway directory for the whole
  run, so every `claude plugin marketplace add/remove` and `claude plugin
  install marc@nexaduo` call lands there instead of mutating your real
  Claude Code config or marketplace registrations. The script prints that
  directory's path; inspect or delete it yourself when done — it is not
  auto-deleted.
- Checks out the arm-A (previous release) worktree in that same scratch
  location instead of `../arm-a`, so it never creates a sibling directory
  next to your checkout. A stale worktree left by a previous interrupted
  local run is detected and cleared before the next one starts, rather than
  silently masked.
- Before spending on the full 30-45 invocations, makes exactly ONE real
  invocation and verifies a telemetry row was actually written under the
  isolated config. If it wasn't, the run aborts immediately with an
  explanation instead of burning the rest of the budget for zero samples
  (this is exactly what happened in run 35046956691).

Run it from a checkout with `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` set and
`git fetch --tags` already done, ideally right after tagging a release so
the walk in `resolve_prev_release_tag()` finds the right baseline.

### CI-only invocation (do not run this locally)

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
