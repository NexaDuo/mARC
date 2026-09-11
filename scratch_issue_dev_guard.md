**Goal & Context**
Implement an opt-in `read-guard.sh` script to intercept large file reads via the `PreToolUse` hook, reducing token waste (Spotify optimization #2).

**Acceptance Criteria**
- Create `core/scripts/read-guard.sh`.
- The script should check if the tool is reading a file. If the file exceeds a threshold (e.g. 350 lines), it must block the read by exiting with code 2 or returning a JSON permissionDecision 'deny'.
- It must permit targeted reads (e.g. if the command pipes to `grep` or specifies `limit`/`offset`).
- It must exempt security and correctness roles (`@sec` and `@rev`), checking the agent's role (e.g. via environment variables or prompt context).
- Ensure it's executed in the `PreToolUse` phase (register it in configuration if needed, or document how it binds).

**Constraints**
- Avoid Spotify's schema bug (Issue #10): use the valid Claude Code hook contract for denying.
