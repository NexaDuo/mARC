# docs/marc/benchmarks/

Raw token-benchmark measurements, committed so they survive the 90-day
retention on the workflow's `token-benchmark-raw-<run-id>` artifact.

## `OPTIN` — the paid-run declaration (issue #309)

`OPTIN`, if present, is a single-line file containing the EXACT tag (e.g.
`v26.9.16`) that is authorized to trigger the real, paid three-arm
measurement on its tag push. `scripts/run_token_benchmark.sh`'s
`is_real_run()` compares this file's (trimmed) content against
`GITHUB_REF_NAME` with anchored, exact-string equality — a prefix
(`v26.9.1`) or superstring (`v26.9.16-rc`) does NOT match, and a patch tag
(`vYY.M.D.MICRO`) never pays regardless of what `OPTIN` says (a hard veto,
preserved from issue #304).

This file is normally **absent**. To measure a specific release: the release
PR adds/updates `OPTIN` to contain that release's tag, alongside the version
bump — that line is what a reviewer is approving when they approve the PR
(it authorizes real API spend on that tag's push). Because the declaration
names one exact tag, it self-expires: the next release's tag won't match
last release's `OPTIN`, so a stale, forgotten, or merged-and-never-removed
file cannot cause an unintended paid run. There is deliberately no boolean
"always pay" mode — every paid tag-triggered run traces back to one
reviewed line naming that run's own tag.

A `workflow_dispatch` run with `real_run=true` does not consult this file at
all; it is an independent, always-available manual opt-in (issue #291),
unchanged by this mechanism.

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
