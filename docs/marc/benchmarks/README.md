# docs/marc/benchmarks/

Raw token-benchmark measurements, committed so they survive the 90-day
retention on the workflow's `token-benchmark-raw-<run-id>` artifact.

Two kinds of subdirectory live here:

- **`<tag>/`** (e.g. `v26.9.15/`) — written automatically by
  `scripts/run_token_benchmark.sh` on a real `release`-event run. This is
  the cache `run_token_benchmark.sh` reads back on the *next* release to
  skip re-running arm A (the previous release's baseline) when the task
  set, model, and CLI version haven't drifted (`task_hash` in
  `manifest.json`). A `workflow_dispatch` real run writes here too, keyed
  by branch name (e.g. `main/`) rather than a tag — expect that directory
  to be overwritten by the next such dispatch; it is a cache, not an
  archive.
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
