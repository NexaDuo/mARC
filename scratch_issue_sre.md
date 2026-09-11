**Goal & Context**
We are applying token-saving optimizations inspired by Spotify's setup (Issue #251). We need a mechanism to measure token consumption before and after these changes to quantify the savings.

**Acceptance Criteria**
- Enhance `core/scripts/token_telemetry_report.py` (or create a new comparison script in `core/scripts/`) to compare a baseline dataset against a post-optimization dataset.
- Output must show a clear before-and-after comparison: percentage saved, total tokens difference, and cost delta.
- Provide brief documentation in the tool/script on how to capture the baseline and run the comparison.
- Must include a regression test.

**Affected Surface**
- `core/scripts/token_telemetry_report.py` (or new script)
- `core/scripts/test_token_telemetry_report.py` (or new test file)

**Constraints**
- Ensure compatibility across harnesses where possible.
- Avoid introducing heavy dependencies.
