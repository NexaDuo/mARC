#!/usr/bin/env python3
import os
import re
import sys

# Mapping of template keys to values for each harness
HARNESS_CONFIGS = {
    "claude-code": {
        "harness_name": "Claude Code",
        "config_dir": ".claude",
        "project_dir_env": "CLAUDE_PROJECT_DIR",
        "plugin_root_env": "CLAUDE_PLUGIN_ROOT",
        "plugin_manifest_path": "harnesses/claude-code/marc/.claude-plugin/plugin.json",
        "plugin_list_command": "claude plugin list",
        "dispatch_instructions": """Once an item is on the board, immediately ping the right specialist in the channel — do not wait for the user's confirmation. Use the Agent tool with the matching subagent_type:
- `engineer` (@dev) — app/service code, IaC, deploy scripts, schema, tests, PRs.
- `sre` (@sre) — deploy, observability, infra health, incident response.
- `design` (@design) — UI screens and UX.
- `security` (@sec) — review a PR diff for vulnerabilities before merge (the mandatory pre-merge gate; see Principles). Read-only reviewer, not an implementer.
- `research` (@research) — fetch external evidence (benchmarks, papers, post-mortems, official docs, comparable products) when a decision lacks internal data and public evidence likely exists — and as the research pass BEFORE the user must configure or choose an external system (the "authoritative docs before the user hunts" principle, made dispatchable). Read-only: its only deliverable is ONE cited brief commented on the motivating issue — no code, no PRs. Its dispatch prompt MUST include: the **precise research question**, the **decision at stake** (the options on the table), the **motivating issue number**, a **timebox** (~8–15 sources read), and the required **output structure** (TL;DR → findings with citations → implications for the decision → coverage & gaps). "Insufficient public evidence" is an acceptable outcome — do not re-dispatch just to force a positive answer.

**Dispatch in the background by default — never block the channel on a specialist.**
Pass `run_in_background: true` on every Agent call. You are re-invoked (notified) when a background agent finishes, and you can resume or continue a running agent by its id. Specialists' work can be slow (a full implement-test-PR cycle, a design pass, a review), so a synchronous dispatch would freeze the main conversation until the subagent returns — the operator must stay responsive to the user while work runs. Concretely:
- "Don't wait for confirmation" ≠ "block on the subagent." The first means you don't pause for the user to say "go" before dispatching; it does not mean you sit synchronously inside the subagent until it returns. Fire the dispatch, then keep the channel live.
- Launch independent items in parallel — multiple background Agent calls in one message (fan-out). They run concurrently; you collect each one as it completes.
- Dependent work (implement → review → merge) stays sequenced, but sequence it via background dispatch + the notification/track loop (step 5), not by blocking synchronously. Kick off the next stage when the prior one reports back.
- Only set `run_in_background: false` for a genuine strict dependency whose result you need before you can do anything else in the same turn — and even then, prefer background if you can. Long-running work is never a reason to block; it's the strongest reason to background.""",
        "isolation_instructions": "using isolation: 'worktree' on the Agent call",
    },
    "antigravity": {
        "harness_name": "Google Antigravity",
        "config_dir": ".agents",
        "project_dir_env": "AGY_PROJECT_DIR",
        "plugin_root_env": "AGY_PLUGIN_ROOT",
        "plugin_manifest_path": "harnesses/antigravity/marc/plugin.json",
        "plugin_list_command": "agy plugin list",
        "dispatch_instructions": """Once an item is on the board, immediately ping the right specialist in the channel — do not wait for the user's confirmation. Use the invoke_subagent tool to spawn the specialist. Set the following fields:
- `TypeName`: `research` for research, or `self` for developer, sre, design, security tasks.
- `Role`: the specialist's role (e.g. `engineer` for @dev, `sre` for @sre, `design` for @design, `security` for @sec, `research` for @research).
- `Prompt`: the detailed prompt for the specialist.
- `Workspace`: `inherit` (or `share` if you want to isolate parallel writing tasks, similar to worktrees).

Dispatch in the background by default — never block the channel on a specialist. The invoke_subagent tool spawns the subagent concurrently. You are re-invoked (notified) when a background agent finishes. Specialists' work can be slow, so you must stay responsive to the user while work runs. Concretely:
- Fire the dispatch using invoke_subagent, then keep the channel live.
- Launch independent items in parallel.
- Dependent work stays sequenced, but sequence it via invoke_subagent dispatch and waiting for notifications, not by blocking synchronously.""",
        "isolation_instructions": "using Workspace='share' in the invoke_subagent call",
    }
}

def compile_file(source_path, dest_path, config):
    with open(source_path, "r", encoding="utf-8") as sf:
        content = sf.read()

    # Replace all {{ key }} placeholders
    for key, value in config.items():
        placeholder = f"{{{{ {key} }}}}"
        content = content.replace(placeholder, value)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    with open(dest_path, "w", encoding="utf-8") as df:
        df.write(content)
    print(f"Compiled: {source_path} -> {dest_path}")

def main():
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    core_dir = os.path.join(base_dir, "core")

    if not os.path.exists(core_dir):
        print(f"Error: core/ directory not found at {core_dir}", file=sys.stderr)
        sys.exit(1)

    for harness, config in HARNESS_CONFIGS.items():
        print(f"\n--- Compiling prompts for harness: {harness} ---")
        harness_dest_root = os.path.join(base_dir, "harnesses", harness, "marc")

        # Walk through the core/ template files
        for root, _, files in os.walk(core_dir):
            for file in files:
                if not file.endswith(".md"):
                    continue
                source_file = os.path.join(root, file)
                rel_path = os.path.relpath(source_file, core_dir)
                dest_file = os.path.join(harness_dest_root, rel_path)
                compile_file(source_file, dest_file, config)

    print("\nPrompt compilation complete for all harnesses.")

if __name__ == "__main__":
    main()
