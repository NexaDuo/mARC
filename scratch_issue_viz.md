**Goal & Context**
Implement advanced visualization strategies for token consumption metrics.

**Acceptance Criteria**
1. **GitHub Step Summary**: Update `.github/workflows/token-benchmark.yml` so the token report is also appended to `$GITHUB_STEP_SUMMARY`.
2. **Dashboard / Pages (Hotsite)**: Create a script (e.g., `core/scripts/generate_telemetry_dashboard.py`) that reads the telemetry JSONL and generates a markdown page (`docs/marc/telemetry.md`) containing a trend chart (using Mermaid.js `xychart-beta` or similar). Add a CI job that runs this on pushes to `main` and commits the result to `docs/marc/`.
3. **README Badge**: The same script/CI should generate a JSON file (`docs/marc/telemetry-badge.json`) with a schema supported by Shields.io Endpoint badges (showing something like `Tokens Saved: 89%`). Add the badge to `README.md`.

**Affected Surface**
- `.github/workflows/token-benchmark.yml`
- `.github/workflows/telemetry-dashboard.yml` (new)
- `core/scripts/generate_telemetry_dashboard.py` (new)
- `README.md`
- `docs/marc/`

**Constraints**
- Keep dependencies minimal (use standard library if possible).
