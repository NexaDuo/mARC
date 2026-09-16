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
- Before spending on the full 30-45 invocations, makes exactly ONE real
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
Claude Code subscription — the ~30-45 invocations a full run makes are
enough to consume your 5-hour usage window. `GEMINI_API_KEY` isn't read
anywhere in this script either; skip it. Run with `git fetch --tags` already
done, ideally right after tagging a release so the walk in
`resolve_prev_release_tag()` finds the right baseline.

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
