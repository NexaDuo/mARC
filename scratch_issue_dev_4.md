**Goal & Context**
Update `@dev` prompt to use disk-first scaffolding (Spotify optimization #4).

**Acceptance Criteria**
- Edit `core/agents/engineer.md`.
- Add explicit instructions telling the `@dev` agent to scaffold repetitive or large boilerplate directly to disk via background scripts or isolated worktrees, avoiding streaming large blocks of predictable syntax into the conversation history.
