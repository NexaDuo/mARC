**Goal & Context**
We need to add support for the `PreToolUse` hook to the `claude-code` compilation. This is the foundation to intercept large I/O reads before tokens are burned (Issue #251).

**Acceptance Criteria**
- Update `scripts/compile_prompts.py` (or `core/scripts/compile_prompts.py`) where `_CC_EVENT_MAP` is defined.
- Add support for `pre_tool_use: "PreToolUse"`.
- Ensure the compiled artifacts are properly regenerated using the script.

**Affected Surface**
- `core/scripts/compile_prompts.py` (and the generated outputs in `harnesses/claude-code/marc/`)

**Constraints**
- Run `python3 scripts/compile_prompts.py` (or similar) to update the generated files before committing.
