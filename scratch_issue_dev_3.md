**Goal & Context**
Make the `read-guard.sh` line limit configurable via `.agents/team.toml` (Spotify optimization #3).

**Acceptance Criteria**
- Update `core/scripts/read-guard.sh` to read `[token_guard] max_read_lines` from `.agents/team.toml` (or `team.toml`), defaulting to 350 if not set.
- Ensure cross-harness compilation works.
