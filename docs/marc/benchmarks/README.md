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
at release time:

```
GITHUB_EVENT_NAME=workflow_dispatch REAL_RUN_INPUT=true scripts/run_token_benchmark.sh
```

This spends real API credit (30-45 live `claude` invocations, depending on
whether a cached baseline for the previous release is found). Run it from a
checkout with `ANTHROPIC_API_KEY`/`GEMINI_API_KEY` set and `git fetch --tags`
already done, ideally right after tagging a release so the walk in
`resolve_prev_release_tag()` finds the right baseline.
