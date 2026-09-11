**Goal & Context**
We need an automated CI workflow that runs a deterministic benchmark session of mARC, captures the token telemetry, compares it against a known baseline, and posts the token reduction/increase percentage as a PR comment. This ensures we can mathematically prove token savings on every PR.

**Acceptance Criteria**
- A new GitHub Actions workflow (e.g., `.github/workflows/token-benchmark.yml`) that triggers on pull requests.
- The workflow runs a minimal, deterministic mARC task to generate telemetry.
- It compares the resulting `token-telemetry.jsonl` against a baseline.
- The output of `token_telemetry_report.py --compare` is attached to the PR as a comment.

**Affected Surface**
- `.github/workflows/token-benchmark.yml` (new file)

**Constraints**
- Must be non-blocking (a failure in the benchmark shouldn't block merges).
