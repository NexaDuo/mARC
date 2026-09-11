**Goal & Context**
Update `.github/workflows/token-benchmark.yml` so that instead of using stubbed JSON telemetry, it executes a real LLM task during a GitHub Release to benchmark token consumption, saving tokens on daily PRs but providing real data on release.

**Acceptance Criteria**
- Modify the workflow triggers: keep the dashboard update for pushes to `main`, but make the real LLM benchmark run on `release` (published).
- Replace the stub generation steps with a real script/step that calls the CLI (e.g. `agy` or `claude`) using `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` from secrets.
- Let the agent do a deterministic task like "read core/scripts/board.py and output a summary".
- Generate the telemetry report and update the dashboard/badge.

**Constraints**
- The repository already has `GEMINI_API_KEY` and `ANTHROPIC_API_KEY` in secrets.
