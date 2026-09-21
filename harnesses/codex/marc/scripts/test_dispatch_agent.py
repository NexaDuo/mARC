#!/usr/bin/env python3
"""Self-test for `dispatch_agent.py` cross-harness dispatch and routing (origin: #239, #241).

Stdlib only (no pytest); run directly:  python3 test_dispatch_agent.py

Deterministic, offline, zero token cost — no real network, no live CLI calls.
Feeds `dispatch_agent.py` synthetic fixtures and asserts:
  * Command generation across claude-code, antigravity, and copilot for all specialist roles
  * Embedded default hybrid specialization matrix across all 3 host harnesses and roles (#241)
  * Route resolution from team.toml [orchestration] (mode='hybrid' vs mode='native')
  * Priority hierarchy: explicit CLI flag > team.toml route > default hybrid matrix
  * CLI availability checking and graceful fallback to host harness
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
    DEFAULT_HYBRID_MATRIX,
    HARNESS_BINARIES,
    ROLE_TO_AGENT,
    build_harness_command,
    detect_native_harness,
    dispatch,
    main as dispatch_main,
    parse_toml,
    resolve_route,
    resolve_target_harness,
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


def test_default_hybrid_matrix() -> None:
    print("\n--- Test: Default hybrid specialization matrix (#241) ---")
    always_which = lambda b: f"/usr/bin/{b}"

    expected_matrix = {
        "claude-code": {
            "dev": "claude-code",
            "engineer": "claude-code",
            "sec": "claude-code",
            "security": "claude-code",
            "rev": "antigravity",
            "review": "antigravity",
            "research": "antigravity",
            "sre": "claude-code",
            "design": "claude-code",
        },
        "antigravity": {
            "dev": "claude-code",
            "engineer": "claude-code",
            "sec": "claude-code",
            "security": "claude-code",
            "rev": "antigravity",
            "review": "antigravity",
            "research": "antigravity",
            "sre": "antigravity",
            "design": "antigravity",
        },
        "copilot": {
            "dev": "claude-code",
            "engineer": "claude-code",
            "sec": "claude-code",
            "security": "claude-code",
            "rev": "antigravity",
            "review": "antigravity",
            "research": "antigravity",
            "sre": "copilot",
            "design": "copilot",
        },
    }

    # Verify constant data structure matches specification
    for host, roles in expected_matrix.items():
        check(host in DEFAULT_HYBRID_MATRIX, f"DEFAULT_HYBRID_MATRIX has host key '{host}'")
        for role, expected_target in roles.items():
            actual = DEFAULT_HYBRID_MATRIX[host].get(role)
            check(actual == expected_target, f"DEFAULT_HYBRID_MATRIX['{host}']['{role}'] == '{expected_target}' (got '{actual}')")

            # Test resolve_target_harness directly
            harness, fallback, reason = resolve_target_harness(
                host_harness=host,
                role=role,
                explicit_harness="auto",
                toml_routes=None,
                toml_mode=None,
                cli_checker=always_which,
            )
            check(harness == expected_target and not fallback, f"resolve_target_harness('{host}', '{role}') -> '{expected_target}' (got '{harness}')")


def test_route_resolution_from_toml() -> None:
    print("\n--- Test: Route resolution from team.toml ---")
    always_which = lambda bin_name: f"/usr/bin/{bin_name}"
    dummy_env_cc = {"CLAUDE_PLUGIN_ROOT": "/plugin"}
    dummy_env_agy = {"ANTIGRAVITY_CONVERSATION_ID": "conv-123"}
    dummy_env_copilot = {"COPILOT_PLUGIN_DATA": "/data"}

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_path = Path(tmpdir)

        # 1. Hybrid mode with explicit user routes
        hybrid_toml = tmp_path / "hybrid_team.toml"
        hybrid_toml.write_text(
            """
[github]
gh_org = "TestOrg"
gh_repo = "TestOrg/test-repo"

[orchestration]
mode = "hybrid"

[orchestration.routes]
dev = "copilot"
research = "claude-code"
sre = "antigravity"
sec = "copilot"
""",
            encoding="utf-8",
        )

        # User routes override default matrix
        harness, fallback, reason = resolve_route("dev", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "copilot" and not fallback, f"hybrid user route 'dev' -> copilot (got {harness})")

        harness, fallback, reason = resolve_route("research", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code" and not fallback, f"hybrid user route 'research' -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("sre", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "antigravity" and not fallback, f"hybrid user route 'sre' -> antigravity (got {harness})")

        # Unrouted role in hybrid mode falls back to DEFAULT_HYBRID_MATRIX
        # On claude-code host: 'rev' -> antigravity, 'design' -> claude-code
        harness, fallback, reason = resolve_route("rev", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "antigravity" and not fallback, f"hybrid unrouted 'rev' on claude-code -> default matrix antigravity (got {harness})")

        harness, fallback, reason = resolve_route("design", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code" and not fallback, f"hybrid unrouted 'design' on claude-code -> default matrix claude-code (got {harness})")

        # Unrouted role on antigravity host: 'design' -> antigravity
        harness, fallback, reason = resolve_route("design", "auto", hybrid_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "antigravity" and not fallback, f"hybrid unrouted 'design' on antigravity -> default matrix antigravity (got {harness})")

        # 2. Native mode (routes and hybrid matrix bypassed)
        native_toml = tmp_path / "native_team.toml"
        native_toml.write_text(
            """
[orchestration]
mode = "native"

[orchestration.routes]
dev = "copilot"
rev = "antigravity"
""",
            encoding="utf-8",
        )
        harness, fallback, reason = resolve_route("dev", "auto", native_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code", f"native mode on claude-code -> native claude-code (got {harness})")

        harness, fallback, reason = resolve_route("rev", "auto", native_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code", f"native mode on claude-code for rev -> native claude-code (got {harness})")

        harness, fallback, reason = resolve_route("dev", "auto", native_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "antigravity", f"native mode on antigravity -> native antigravity (got {harness})")

        # 3. Missing / empty team.toml defaults to DEFAULT_HYBRID_MATRIX
        empty_toml = tmp_path / "empty_team.toml"
        empty_toml.write_text("", encoding="utf-8")

        # Claude Code host
        harness, fallback, reason = resolve_route("dev", "auto", empty_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (claude-code host) for dev -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("rev", "auto", empty_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "antigravity", f"empty team.toml (claude-code host) for rev -> antigravity (got {harness})")

        harness, fallback, reason = resolve_route("research", "auto", empty_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "antigravity", f"empty team.toml (claude-code host) for research -> antigravity (got {harness})")

        # Antigravity host
        harness, fallback, reason = resolve_route("dev", "auto", empty_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (antigravity host) for dev -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("sre", "auto", empty_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "antigravity", f"empty team.toml (antigravity host) for sre -> antigravity (got {harness})")

        # Copilot host
        harness, fallback, reason = resolve_route("dev", "auto", empty_toml, env=dummy_env_copilot, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (copilot host) for dev -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("rev", "auto", empty_toml, env=dummy_env_copilot, which_fn=always_which)
        check(harness == "antigravity", f"empty team.toml (copilot host) for rev -> antigravity (got {harness})")

        harness, fallback, reason = resolve_route("sre", "auto", empty_toml, env=dummy_env_copilot, which_fn=always_which)
        check(harness == "copilot", f"empty team.toml (copilot host) for sre -> copilot (got {harness})")

        # 4. Explicit CLI flag override beats team.toml routes and default matrix
        harness, fallback, reason = resolve_route("dev", "antigravity", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "antigravity", f"explicit --harness antigravity overrides team.toml (got {harness})")

        harness, fallback, reason = resolve_route("rev", "claude-code", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code", f"explicit --harness claude-code overrides default matrix (got {harness})")

        harness, fallback, reason = resolve_route("dev", "native", hybrid_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "antigravity", f"explicit --harness native resolves to host harness (got {harness})")


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
    def which_only_claude(b):
        return "/usr/bin/claude" if b == "claude" else None

    def which_only_agy(b):
        return "/usr/bin/agy" if b == "agy" else None

    def which_only_copilot(b):
        return "/usr/bin/copilot" if b == "copilot" else None

    # Scenario 1: On claude-code host, default matrix routes 'rev' -> antigravity ('agy').
    # But only 'claude' CLI is available. Falls back to host harness (claude-code).
    env_cc = {"CLAUDE_PLUGIN_ROOT": "/plugin"}
    harness, fallback, reason = resolve_route(
        role="rev",
        requested_harness="auto",
        env=env_cc,
        which_fn=which_only_claude,
    )
    check(harness == "claude-code", f"matrix rev fallback resolved to claude-code (got {harness})")
    check(fallback is True, "fallback flag is True")
    check(reason is not None and "agy" in reason, f"fallback reason mentions agy ({reason})")

    # Scenario 2: On antigravity host, default matrix routes 'dev' -> claude-code ('claude').
    # But only 'agy' CLI is available. Falls back to host harness (antigravity).
    env_agy = {"ANTIGRAVITY_CONVERSATION_ID": "conv-123"}
    harness, fallback, reason = resolve_route(
        role="dev",
        requested_harness="auto",
        env=env_agy,
        which_fn=which_only_agy,
    )
    check(harness == "antigravity", f"matrix dev fallback on agy host resolved to antigravity (got {harness})")
    check(fallback is True, "fallback flag is True")
    check(reason is not None and "claude" in reason, f"fallback reason mentions claude ({reason})")

    # Scenario 3: On copilot host, default matrix routes 'research' -> antigravity ('agy').
    # But only 'copilot' CLI is available. Falls back to host harness (copilot).
    env_copilot = {"COPILOT_PLUGIN_DATA": "/data"}
    harness, fallback, reason = resolve_route(
        role="research",
        requested_harness="auto",
        env=env_copilot,
        which_fn=which_only_copilot,
    )
    check(harness == "copilot", f"matrix research fallback on copilot host resolved to copilot (got {harness})")
    check(fallback is True, "fallback flag is True")
    check(reason is not None and "agy" in reason, f"fallback reason mentions agy ({reason})")

    # Scenario 4: User explicitly requests 'copilot', but copilot is not on PATH.
    # Native host is claude-code. Falls back to claude-code.
    harness, fallback, reason = resolve_route(
        role="sre",
        requested_harness="copilot",
        env=env_cc,
        which_fn=which_only_claude,
    )
    check(harness == "claude-code", f"explicit copilot fallback resolved to claude-code (got {harness})")
    check(fallback is True, "fallback flag is True")
    check(reason is not None and "copilot" in reason, f"fallback reason mentions copilot ({reason})")

    # Verify command builder generates fallback command in dispatch()
    res = dispatch(
        role="rev",
        prompt="Review PR #123",
        harness="auto",
        dry_run=True,
        env=env_cc,
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
    test_default_hybrid_matrix()
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
