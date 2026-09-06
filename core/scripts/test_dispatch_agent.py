#!/usr/bin/env python3
"""Self-test for `dispatch_agent.py` cross-harness dispatch and routing (origin: #239).

Stdlib only (no pytest); run directly:  python3 test_dispatch_agent.py

Deterministic, offline, zero token cost — no real network, no live CLI calls.
Feeds `dispatch_agent.py` synthetic fixtures and asserts:
  * Command generation across claude-code, antigravity, and copilot for all specialist roles
  * Route resolution from team.toml [orchestration] (mode='hybrid' vs mode='native')
  * CLI availability checking and graceful fallback logic
  * Timeout handling and error reporting
  * Dry-run mode and JSON serialization contract
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dispatch_agent import (  # noqa: E402
    CANONICAL_ROLES,
    HARNESS_BINARIES,
    ROLE_TO_AGENT,
    build_harness_command,
    detect_native_harness,
    dispatch,
    main as dispatch_main,
    parse_toml,
    resolve_route,
)

_failures: List[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def test_command_generation() -> None:
    print("\n--- Test: Command generation for all harnesses ---")
    prompt = "Review PR #42 for vulnerabilities and logic bugs"

    # 1. claude-code
    roles_claude = {
        "dev": ["claude", "--dangerously-skip-permissions", "--agent", "engineer", "-p", prompt],
        "engineer": ["claude", "--dangerously-skip-permissions", "--agent", "engineer", "-p", prompt],
        "sre": ["claude", "--dangerously-skip-permissions", "--agent", "sre", "-p", prompt],
        "design": ["claude", "--dangerously-skip-permissions", "--agent", "design", "-p", prompt],
        "sec": ["claude", "--dangerously-skip-permissions", "--agent", "security", "-p", prompt],
        "security": ["claude", "--dangerously-skip-permissions", "--agent", "security", "-p", prompt],
        "rev": ["claude", "--dangerously-skip-permissions", "--agent", "review", "-p", prompt],
        "review": ["claude", "--dangerously-skip-permissions", "--agent", "review", "-p", prompt],
        "research": ["claude", "--dangerously-skip-permissions", "--agent", "research", "-p", prompt],
    }
    for r, expected in roles_claude.items():
        cmd = build_harness_command("claude-code", r, prompt)
        check(cmd == expected, f"claude-code command for role '{r}': {cmd}")

    # 2. antigravity
    roles_agy = {
        "dev": ["agy", "--dangerously-skip-permissions", "--agent", "engineer", "-p", prompt],
        "sre": ["agy", "--dangerously-skip-permissions", "--agent", "sre", "-p", prompt],
        "design": ["agy", "--dangerously-skip-permissions", "--agent", "design", "-p", prompt],
        "sec": ["agy", "--dangerously-skip-permissions", "--agent", "security", "-p", prompt],
        "rev": ["agy", "--dangerously-skip-permissions", "--agent", "review", "-p", prompt],
        "research": ["agy", "--dangerously-skip-permissions", "--agent", "research", "-p", prompt],
    }
    for r, expected in roles_agy.items():
        cmd = build_harness_command("antigravity", r, prompt)
        check(cmd == expected, f"antigravity command for role '{r}': {cmd}")

    # 3. copilot
    for r in ["dev", "sre", "design", "sec", "rev", "research"]:
        cmd = build_harness_command("copilot", r, prompt)
        check(cmd == ["copilot", "--prompt", prompt], f"copilot command for role '{r}': {cmd}")

    # 4. Unknown harness throws ValueError
    try:
        build_harness_command("unknown-harness", "dev", prompt)
        check(False, "unknown harness should raise ValueError")
    except ValueError:
        check(True, "unknown harness correctly raised ValueError")


def test_route_resolution_from_toml() -> None:
    print("\n--- Test: Route resolution from team.toml ---")
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Hybrid mode with routes
        hybrid_toml = tmp_path / "hybrid_team.toml"
        hybrid_toml.write_text(
            """
[github]
gh_org = "TestOrg"
gh_repo = "TestOrg/test-repo"

[orchestration]
mode = "hybrid"

[orchestration.routes]
dev = "claude-code"
research = "antigravity"
sre = "copilot"
sec = "claude-code"
""",
            encoding="utf-8",
        )

        always_which = lambda bin_name: f"/usr/bin/{bin_name}"
        dummy_env = {"CLAUDE_PLUGIN_ROOT": "/plugin"}

        harness, fallback, reason = resolve_route("dev", "auto", hybrid_toml, env=dummy_env, which_fn=always_which)
        check(harness == "claude-code" and not fallback, f"hybrid route 'dev' -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("research", "auto", hybrid_toml, env=dummy_env, which_fn=always_which)
        check(harness == "antigravity" and not fallback, f"hybrid route 'research' -> antigravity (got {harness})")

        harness, fallback, reason = resolve_route("sre", "auto", hybrid_toml, env=dummy_env, which_fn=always_which)
        check(harness == "copilot" and not fallback, f"hybrid route 'sre' -> copilot (got {harness})")

        # Unrouted role defaults to native
        harness, fallback, reason = resolve_route("design", "auto", hybrid_toml, env=dummy_env, which_fn=always_which)
        check(harness == "claude-code" and not fallback, f"hybrid unrouted 'design' -> native (claude-code) (got {harness})")

        # 2. Native mode (routes ignored)
        native_toml = tmp_path / "native_team.toml"
        native_toml.write_text(
            """
[orchestration]
mode = "native"

[orchestration.routes]
dev = "copilot"
""",
            encoding="utf-8",
        )
        harness, fallback, reason = resolve_route("dev", "auto", native_toml, env=dummy_env, which_fn=always_which)
        check(harness == "claude-code", f"native mode -> native (claude-code) (got {harness})")

        # 3. Missing / empty team.toml
        empty_toml = tmp_path / "empty_team.toml"
        empty_toml.write_text("", encoding="utf-8")
        harness, fallback, reason = resolve_route("dev", "auto", empty_toml, env=dummy_env, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml -> native (claude-code) (got {harness})")

        # 4. Explicit CLI flag override beats team.toml routes
        harness, fallback, reason = resolve_route("dev", "antigravity", hybrid_toml, env=dummy_env, which_fn=always_which)
        check(harness == "antigravity", f"explicit --harness antigravity overrides team.toml (got {harness})")


def test_native_harness_detection() -> None:
    print("\n--- Test: Native harness detection ---")
    mock_which = lambda b: f"/bin/{b}"

    # 1. Antigravity env vars
    agy_env = {"ANTIGRAVITY_CONVERSATION_ID": "conv-123"}
    check(detect_native_harness(agy_env, mock_which) == "antigravity", "detects antigravity from ANTIGRAVITY_CONVERSATION_ID")
    agy_env2 = {"AGY_PLUGIN_ROOT": "/path/to/plugin"}
    check(detect_native_harness(agy_env2, mock_which) == "antigravity", "detects antigravity from AGY_PLUGIN_ROOT")

    # 2. Copilot env vars
    copilot_env = {"COPILOT_PLUGIN_DATA": "/data"}
    check(detect_native_harness(copilot_env, mock_which) == "copilot", "detects copilot from COPILOT_PLUGIN_DATA")

    # 3. Claude Code env vars
    cc_env = {"CLAUDE_PLUGIN_ROOT": "/root"}
    check(detect_native_harness(cc_env, mock_which) == "claude-code", "detects claude-code from CLAUDE_PLUGIN_ROOT")

    # 4. Binary fallback when env vars unset
    def which_only_agy(b):
        return "/bin/agy" if b == "agy" else None

    def which_only_copilot(b):
        return "/bin/copilot" if b == "copilot" else None

    check(detect_native_harness({}, which_only_agy) == "antigravity", "detects antigravity from PATH when env unset")
    check(detect_native_harness({}, which_only_copilot) == "copilot", "detects copilot from PATH when env unset")


def test_cli_availability_and_fallback() -> None:
    print("\n--- Test: CLI availability and fallback logic ---")
    # Scenario: Route targets 'copilot' (binary: 'copilot'), but copilot is NOT in PATH.
    # Native environment is claude-code (binary: 'claude' is in PATH).
    def which_only_claude(b):
        return "/usr/bin/claude" if b == "claude" else None

    env = {"CLAUDE_PLUGIN_ROOT": "/plugin"}
    harness, fallback, reason = resolve_route(
        role="sre",
        requested_harness="copilot",
        env=env,
        which_fn=which_only_claude,
    )
    check(harness == "claude-code", f"fallback harness resolved to claude-code (got {harness})")
    check(fallback is True, "fallback flag is True")
    check(reason is not None and "copilot" in reason, f"fallback reason mentions copilot ({reason})")

    # Verify command builder generates fallback command in dispatch()
    res = dispatch(
        role="sre",
        prompt="Check logs",
        harness="copilot",
        dry_run=True,
        env=env,
        which_fn=which_only_claude,
    )
    check(res["fallback"] is True, "dispatch result indicates fallback")
    check(res["harness"] == "claude-code", f"dispatch result resolved to claude-code (got {res['harness']})")
    check(res["command"][0] == "claude", f"command uses fallback binary 'claude' (got {res['command'][0]})")


def test_dry_run_and_execution() -> None:
    print("\n--- Test: Dry-run and execution mocking ---")
    always_which = lambda b: f"/bin/{b}"
    env = {"CLAUDE_PLUGIN_ROOT": "/plugin"}

    # Dry-run
    mock_runner = MagicMock()
    res = dispatch(
        role="dev",
        prompt="Fix bug",
        harness="claude-code",
        dry_run=True,
        env=env,
        which_fn=always_which,
        runner_fn=mock_runner,
    )
    check(res["dry_run"] is True, "dry_run is True in result")
    check(res["exit_code"] == 0, "dry_run exit_code is 0")
    check(mock_runner.call_count == 0, "runner_fn was not called during dry_run")

    # Successful execution
    proc_mock = subprocess.CompletedProcess(
        args=["claude"],
        returncode=0,
        stdout="Subagent completed successfully\n",
        stderr="",
    )
    mock_runner = MagicMock(return_value=proc_mock)
    res = dispatch(
        role="dev",
        prompt="Fix bug",
        harness="claude-code",
        dry_run=False,
        env=env,
        which_fn=always_which,
        runner_fn=mock_runner,
    )
    check(res["exit_code"] == 0, "execution exit_code is 0")
    check(res["success"] is True, "execution success is True")
    check("Subagent completed" in res["stdout"], "execution captured stdout")
    check(mock_runner.call_count == 1, "runner_fn was called once")


def test_timeout_handling() -> None:
    print("\n--- Test: Timeout handling ---")
    always_which = lambda b: f"/bin/{b}"
    env = {"CLAUDE_PLUGIN_ROOT": "/plugin"}

    def timeout_runner(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0], timeout=kwargs.get("timeout", 10.0))

    res = dispatch(
        role="dev",
        prompt="Long task",
        harness="claude-code",
        timeout=15.0,
        dry_run=False,
        env=env,
        which_fn=always_which,
        runner_fn=timeout_runner,
    )
    check(res["exit_code"] == 124, f"timeout returns exit code 124 (got {res['exit_code']})")
    check(res["success"] is False, "timeout success is False")
    check("Timeout" in res.get("error", ""), f"timeout error recorded: {res.get('error')}")


def test_json_and_cli_interface() -> None:
    print("\n--- Test: JSON output and CLI main() ---")
    stdout_buf = io.StringIO()
    old_stdout = sys.stdout
    try:
        sys.stdout = stdout_buf
        code = dispatch_main(["--role", "dev", "--prompt", "Build feature", "--harness", "claude-code", "--dry-run", "--json"])
    finally:
        sys.stdout = old_stdout

    check(code == 0, f"CLI dry-run exit code is 0 (got {code})")
    output_str = stdout_buf.getvalue()
    try:
        payload = json.loads(output_str)
        check(payload.get("role") == "dev", "JSON payload contains correct role")
        check(payload.get("mapped_agent") == "engineer", "JSON payload contains correct mapped_agent")
        check(payload.get("harness") == "claude-code", "JSON payload contains correct harness")
        check(isinstance(payload.get("command"), list), "JSON payload contains command list")
        check("claude" in payload.get("command_str", ""), "JSON payload contains command_str")
        check(payload.get("dry_run") is True, "JSON payload indicates dry_run: true")
    except json.JSONDecodeError as e:
        check(False, f"CLI did not emit valid JSON: {e}")


def main() -> int:
    test_command_generation()
    test_route_resolution_from_toml()
    test_native_harness_detection()
    test_cli_availability_and_fallback()
    test_dry_run_and_execution()
    test_timeout_handling()
    test_json_and_cli_interface()

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print("\ndispatch_agent self-test: OK (all test cases passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
