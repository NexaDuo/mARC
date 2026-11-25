#!/usr/bin/env python3
"""Self-test for `dispatch_agent.py` cross-harness dispatch and routing (origin: #239, #241).

Stdlib only (no pytest); run directly:  python3 test_dispatch_agent.py

Deterministic, offline, zero token cost — no real network, no live CLI calls.
Feeds `dispatch_agent.py` synthetic fixtures and asserts:
  * Command generation across all four host harnesses for specialist roles
  * Embedded default hybrid specialization matrix across all 4 host harnesses and roles (#241)
  * Route resolution from team.toml [orchestration] (mode='hybrid' vs mode='native')
  * Priority hierarchy: explicit CLI flag > team.toml route > default hybrid matrix
  * CLI availability checking and graceful fallback to host harness
  * Timeout handling and error reporting
  * Dry-run mode and JSON serialization contract
  * Read-only roles never route to antigravity; fail closed when no enforcing harness (#323)
  * Detached `start`/`wait` jobs against a real stub harness subprocess (#352)
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from dispatch_agent import (  # noqa: E402
    CANONICAL_ROLES,
    CODEX_READONLY_ROLES,
    DEFAULT_HYBRID_MATRIX,
    HARNESS_BINARIES,
    NON_ENFORCING_HARNESSES,
    READ_ONLY_ROLES,
    ROLE_TO_AGENT,
    ModelPinError,
    ReadOnlyRoutingError,
    UnknownRoleError,
    _check_writable_dir,
    _validate_spec,
    load_agy_model,
    load_codex_pins,
    build_codex_readonly_prompt,
    build_harness_command,
    codex_readonly_opt_in,
    is_forbidden_readonly_route,
    load_codex_persona,
    prepare_dispatch,
    resolve_default_timeout,
    resolve_route_detailed,
    detect_native_harness,
    dispatch,
    main as dispatch_main,
    normalize_role,
    parse_toml,
    resolve_route,
    resolve_target_harness,
    resolve_target_harness_detailed,
    harnesses_line,
    load_writer_opt_in,
    probe_harnesses,
    record_block,
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
    # Personas ship as skills on Antigravity and agy headless ignores `--agent`
    # (#323), so the command names the persona skill in the prompt instead.
    # #385: sre/design ship an Antigravity descriptor (`--model <slug>`);
    # engineer has none, so it keeps the flag-less argv.
    def agy_cmd(skill: str, model: Optional[str] = None) -> list:
        return ["agy", "--dangerously-skip-permissions",
                *(["--model", model] if model else []), "-p",
                f"Read the '{skill}' skill instructions before proceeding, "
                f"then act as the mARC {skill} specialist. {prompt}"]
    roles_agy = {
        "dev": agy_cmd("engineer"),
        "engineer": agy_cmd("engineer"),
        "sre": agy_cmd("sre", "gemini-4-argon-high"),
        "design": agy_cmd("design", "gemini-4-argon-high"),
    }
    for r, expected in roles_agy.items():
        cmd = build_harness_command("antigravity", r, prompt)
        check(cmd == expected, f"antigravity command for role '{r}': {cmd}")
        check("--agent" not in cmd, f"antigravity command for '{r}' has no ignored --agent flag")
    # Read-only roles never get an agy command, even if called directly (#323).
    for r in ["sec", "security", "rev", "review", "research", "bulk-reader"]:
        try:
            build_harness_command("antigravity", r, prompt)
            check(False, f"antigravity command for read-only '{r}' should raise")
        except ReadOnlyRoutingError:
            check(True, f"antigravity command for read-only '{r}' raises")

    # 3. copilot
    for r in ["dev", "sre", "design"]:
        cmd = build_harness_command("copilot", r, prompt)
        check(cmd == ["copilot", "--prompt", prompt], f"copilot command for role '{r}': {cmd}")
    # Read-only roles never get a copilot command either: one shared #323
    # predicate (is_forbidden_readonly_route) now backs every call site (#352).
    for r in ["sec", "rev", "research", "bulk-reader"]:
        try:
            build_harness_command("copilot", r, prompt)
            check(False, f"copilot command for read-only '{r}' should raise")
        except ReadOnlyRoutingError:
            check(True, f"copilot command for read-only '{r}' raises")

    # 4. Codex writers use an explicit sandbox plus the persona pins (#385);
    #    read-only roles are routed away.
    for r in ["dev", "sre", "design"]:
        cmd = build_harness_command("codex", r, prompt)
        expected = ["codex", "exec", "--worktree", "--sandbox", "workspace-write",
                    "-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="high"',
                    f"Act as the mARC {ROLE_TO_AGENT[r]} specialist. {prompt}"]
        check(cmd == expected, f"codex writer command for '{r}': {cmd}")

    # 5. Unknown harness throws ValueError
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
            "rev": "claude-code",
            "review": "claude-code",
            "research": "claude-code",
            "sre": "claude-code",
            "design": "claude-code",
            "bulk-reader": "claude-code",
        },
        "antigravity": {
            "dev": "claude-code",
            "engineer": "claude-code",
            "sec": "claude-code",
            "security": "claude-code",
            "rev": "claude-code",
            "review": "claude-code",
            "research": "claude-code",
            "sre": "antigravity",
            "design": "antigravity",
            "bulk-reader": "claude-code",
        },
        "copilot": {
            "dev": "claude-code",
            "engineer": "claude-code",
            "sec": "claude-code",
            "security": "claude-code",
            "rev": "claude-code",
            "review": "claude-code",
            "research": "claude-code",
            "sre": "copilot",
            "design": "copilot",
            "bulk-reader": "claude-code",
        },
        "codex": {
            "dev": "codex",
            "engineer": "codex",
            "sec": "claude-code",
            "security": "claude-code",
            "rev": "claude-code",
            "review": "claude-code",
            "research": "claude-code",
            "sre": "codex",
            "design": "codex",
            "bulk-reader": "claude-code",
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
        # #409: a team.toml writer route off the native harness needs the operator opt-in.
        check(harness == "claude-code" and fallback and "opt-in" in str(reason),
              f"hybrid user route 'dev' -> copilot falls back native without opt-in, flagged (got {harness})")

        harness, fallback, reason = resolve_route("research", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code" and not fallback, f"hybrid user route 'research' -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("sre", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code" and fallback,
              f"hybrid user route 'sre' -> antigravity falls back native without opt-in, flagged (got {harness})")

        # Unrouted role in hybrid mode falls back to DEFAULT_HYBRID_MATRIX
        # On claude-code host: 'rev' -> claude-code (#323), 'design' -> claude-code
        harness, fallback, reason = resolve_route("rev", "auto", hybrid_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code" and not fallback, f"hybrid unrouted 'rev' on claude-code -> default matrix claude-code (got {harness})")

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
        check(harness == "claude-code", f"empty team.toml (claude-code host) for rev -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("research", "auto", empty_toml, env=dummy_env_cc, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (claude-code host) for research -> claude-code (got {harness})")

        # Antigravity host
        harness, fallback, reason = resolve_route("dev", "auto", empty_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (antigravity host) for dev -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("sre", "auto", empty_toml, env=dummy_env_agy, which_fn=always_which)
        check(harness == "antigravity", f"empty team.toml (antigravity host) for sre -> antigravity (got {harness})")

        # Copilot host
        harness, fallback, reason = resolve_route("dev", "auto", empty_toml, env=dummy_env_copilot, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (copilot host) for dev -> claude-code (got {harness})")

        harness, fallback, reason = resolve_route("rev", "auto", empty_toml, env=dummy_env_copilot, which_fn=always_which)
        check(harness == "claude-code", f"empty team.toml (copilot host) for rev -> claude-code (got {harness})")

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

    # 4. Codex env vars
    codex_env = {"PLUGIN_ROOT": "/plugin"}
    check(detect_native_harness(codex_env, mock_which) == "codex", "detects codex from PLUGIN_ROOT")

    # 5. Binary fallback when env vars unset
    def which_only_agy(b):
        return "/bin/agy" if b == "agy" else None

    def which_only_copilot(b):
        return "/bin/copilot" if b == "copilot" else None

    def which_only_codex(b):
        return "/bin/codex" if b == "codex" else None

    check(detect_native_harness({}, which_only_agy) == "antigravity", "detects antigravity from PATH when env unset")
    check(detect_native_harness({}, which_only_copilot) == "copilot", "detects copilot from PATH when env unset")
    check(detect_native_harness({}, which_only_codex) == "codex", "detects codex from PATH when env unset")


def test_cli_availability_and_fallback() -> None:
    print("\n--- Test: CLI availability and fallback logic ---")
    def which_only_claude(b):
        return "/usr/bin/claude" if b == "claude" else None

    def which_only_agy(b):
        return "/usr/bin/agy" if b == "agy" else None

    def which_only_copilot(b):
        return "/usr/bin/copilot" if b == "copilot" else None

    # Scenario 1: On claude-code host, 'sre' is explicitly requested on antigravity ('agy').
    # But only 'claude' CLI is available. Falls back to host harness (claude-code).
    # (Read-only roles no longer route to agy at all, #323.)
    env_cc = {"CLAUDE_PLUGIN_ROOT": "/plugin"}
    harness, fallback, reason = resolve_route(
        role="sre",
        requested_harness="antigravity",
        env=env_cc,
        which_fn=which_only_claude,
    )
    check(harness == "claude-code", f"explicit agy sre fallback resolved to claude-code (got {harness})")
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

    # Scenario 3: On copilot host, default matrix routes 'design' -> copilot, but only
    # 'claude' is on PATH. Non-read-only role falls back to an available harness.
    env_copilot = {"COPILOT_PLUGIN_DATA": "/data"}
    harness, fallback, reason = resolve_route(
        role="design",
        requested_harness="auto",
        env=env_copilot,
        which_fn=which_only_claude,
    )
    check(harness == "claude-code", f"matrix design fallback on copilot host resolved to claude-code (got {harness})")
    check(fallback is True, "fallback flag is True")
    check(reason is not None and "copilot" in reason, f"fallback reason mentions copilot ({reason})")

    # Scenario 3b: read-only 'research' on a copilot host with only 'copilot' on PATH
    # no longer falls back to copilot (it does not apply agent definitions, #323).
    try:
        resolve_route(role="research", requested_harness="auto", env=env_copilot,
                      which_fn=which_only_copilot)
        check(False, "read-only research on copilot-only host must fail closed")
    except ReadOnlyRoutingError as e:
        check("#323" in str(e), f"read-only research on copilot-only host fails closed ({e})")

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
        role="sre",
        prompt="Check deploy for PR #123",
        harness="antigravity",
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


def _capture_stderr(fn, *args, **kwargs):
    """Run fn and return (result, stderr_text, exception_or_None)."""
    buf = io.StringIO()
    old = sys.stderr
    sys.stderr = buf
    exc = None
    result = None
    try:
        result = fn(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 — surfaced to the caller for assertions
        exc = e
    finally:
        sys.stderr = old
    return result, buf.getvalue(), exc


def test_read_only_roles_fail_closed() -> None:
    """Regression for #323: read-only roles never dispatch to antigravity."""
    print("\n--- Test: read-only roles never route to antigravity (#323) ---")
    always_which = lambda b: f"/usr/bin/{b}"

    def which_only_agy(b):
        return "/usr/bin/agy" if b == "agy" else None

    def which_only_copilot(b):
        return "/usr/bin/copilot" if b == "copilot" else None

    env_cc = {"CLAUDE_PLUGIN_ROOT": "/plugin"}
    env_agy = {"ANTIGRAVITY_CONVERSATION_ID": "conv-123"}
    env_copilot = {"COPILOT_PLUGIN_DATA": "/data"}

    # The constant names the canonical read-only set; aliases resolve into it.
    check(
        {"rev", "research", "sec", "bulk-reader"} <= set(READ_ONLY_ROLES),
        f"READ_ONLY_ROLES covers rev/research/sec/bulk-reader ({sorted(READ_ONLY_ROLES)})",
    )
    read_only_aliases = [r for r in CANONICAL_ROLES if CANONICAL_ROLES[r] in READ_ONLY_ROLES]
    for alias in ("rev", "review", "research", "sec", "security", "bulk-reader"):
        check(alias in read_only_aliases, f"'{alias}' resolves to a read-only role")

    # 1. The matrix routes every read-only role/alias to claude-code in EVERY host row,
    #    both in the data and through the real routing function.
    for host in DEFAULT_HYBRID_MATRIX:
        for alias in read_only_aliases:
            check(
                DEFAULT_HYBRID_MATRIX[host].get(alias) == "claude-code",
                f"DEFAULT_HYBRID_MATRIX['{host}']['{alias}'] == 'claude-code'",
            )
            (h, fb, _), _, exc = _capture_stderr(
                resolve_target_harness, host, alias, "auto", None, None, always_which,
            )
            check(exc is None and h == "claude-code" and not fb,
                  f"auto route for read-only '{alias}' on host '{host}' -> claude-code (got {h})")

    # 2. Explicit --harness antigravity for a read-only role is re-routed, with a #323 diagnostic.
    for alias in read_only_aliases:
        (h, fb, reason), err, exc = _capture_stderr(
            resolve_route, alias, "antigravity", None, env_cc, always_which,
        )
        check(exc is None and h == "claude-code",
              f"explicit --harness antigravity for '{alias}' re-routed to claude-code (got {h})")
        check(fb is True and reason is not None and "#323" in reason,
              f"re-route of '{alias}' flagged with #323 reason ({reason})")
        check("#323" in err and "Re-routing" in err,
              f"stderr diagnostic cites #323 for '{alias}' ({err.strip()!r})")

    # 3. team.toml route to antigravity for a read-only role is re-routed too.
    with tempfile.TemporaryDirectory() as tmpdir:
        toml = Path(tmpdir) / "team.toml"
        toml.write_text(
            '[orchestration]\nmode = "hybrid"\n\n[orchestration.routes]\n'
            'rev = "antigravity"\nresearch = "antigravity"\nsec = "antigravity"\n'
            'sre = "antigravity"\n',
            encoding="utf-8",
        )
        for role in ("rev", "review", "research", "sec", "security"):
            (h, fb, reason), err, exc = _capture_stderr(
                resolve_route, role, "auto", toml, env_cc, always_which,
            )
            check(exc is None and h == "claude-code" and "#323" in err,
                  f"team.toml route antigravity for '{role}' re-routed to claude-code (got {h})")

        # Native mode on an antigravity host would also land rev on agy: re-routed.
        native = Path(tmpdir) / "native.toml"
        native.write_text('[orchestration]\nmode = "native"\n', encoding="utf-8")
        (h, fb, reason), err, exc = _capture_stderr(
            resolve_route, "rev", "auto", native, env_agy, always_which,
        )
        check(exc is None and h == "claude-code",
              f"native mode on agy host re-routes 'rev' to claude-code (got {h})")

        # The full dispatch() path produces a claude command, never agy.
        res, err, exc = _capture_stderr(
            dispatch, "rev", "Review PR #1", "antigravity", 300.0, True, str(toml), env_cc, always_which,
        )
        check(exc is None and res["harness"] == "claude-code" and res["command"][0] == "claude",
              f"dispatch(rev, --harness antigravity) builds a claude command ({res and res['command'][:1]})")

    # 4. Host=copilot, claude missing: copilot does not apply agent definitions
    #    either, so a read-only role fails closed rather than landing on copilot or agy.
    _, err, exc = _capture_stderr(
        resolve_route, "rev", "antigravity", None, env_copilot,
        lambda b: f"/usr/bin/{b}" if b in ("copilot", "agy") else None,
    )
    check(isinstance(exc, ReadOnlyRoutingError),
          f"read-only role with only copilot/agy available fails closed ({exc!r})")

    # 4b. Copilot is non-enforcing: explicit --harness copilot, a team.toml route to
    #     copilot, and native mode on a copilot host all re-route read-only roles.
    check("copilot" in NON_ENFORCING_HARNESSES, "copilot is listed in NON_ENFORCING_HARNESSES")
    for alias in read_only_aliases:
        (h, fb, reason), err, exc = _capture_stderr(
            resolve_route, alias, "copilot", None, env_cc, always_which,
        )
        check(exc is None and h == "claude-code" and "#323" in err and "Re-routing" in err,
              f"explicit --harness copilot for '{alias}' re-routed to claude-code (got {h})")
    with tempfile.TemporaryDirectory() as tmpdir:
        toml = Path(tmpdir) / "team.toml"
        toml.write_text(
            '[orchestration]\nmode = "hybrid"\n\n[orchestration.routes]\n'
            'rev = "copilot"\nresearch = "copilot"\nsec = "copilot"\n',
            encoding="utf-8",
        )
        for role in ("rev", "research", "sec"):
            (h, fb, reason), err, exc = _capture_stderr(
                resolve_route, role, "auto", toml, env_cc, always_which,
            )
            check(exc is None and h == "claude-code" and "#323" in err,
                  f"team.toml route copilot for '{role}' re-routed to claude-code (got {h})")
        native = Path(tmpdir) / "native.toml"
        native.write_text('[orchestration]\nmode = "native"\n', encoding="utf-8")
        for role in ("rev", "bulk-reader"):
            (h, fb, reason), err, exc = _capture_stderr(
                resolve_route, role, "auto", native, env_copilot, always_which,
            )
            check(exc is None and h == "claude-code",
                  f"native mode on copilot host re-routes '{role}' to claude-code (got {h})")

    # 4c. JSON policy fields separate the #323 override from a missing-CLI fallback.
    res, _, exc = _capture_stderr(
        dispatch, "rev", "x", "antigravity", 300.0, True, None, env_cc, always_which,
    )
    check(exc is None and res["policy_reroute"] is True and "#323" in (res["policy_reason"] or "")
          and res["fallback"] is True,
          f"policy re-route: policy_reroute=True, fallback kept True ({res and res.get('policy_reroute')})")
    res, _, exc = _capture_stderr(
        dispatch, "sre", "x", "antigravity", 300.0, True, None, env_cc,
        lambda b: "/usr/bin/claude" if b == "claude" else None,
    )
    check(exc is None and res["fallback"] is True and res["policy_reroute"] is False
          and res["policy_reason"] is None,
          f"CLI-missing fallback: fallback=True, policy_reroute=False ({res and res.get('policy_reroute')})")
    res, _, exc = _capture_stderr(
        dispatch, "dev", "x", "claude-code", 300.0, True, None, env_cc, always_which,
    )
    check(exc is None and res["fallback"] is False and res["policy_reroute"] is False,
          "no deviation: fallback=False, policy_reroute=False")

    # 5. No enforcing harness available (agy host, only agy on PATH): fail closed.
    for alias in ("rev", "research", "sec"):
        _, err, exc = _capture_stderr(resolve_route, alias, "auto", None, env_agy, which_only_agy)
        check(isinstance(exc, ReadOnlyRoutingError) and "#323" in str(exc),
              f"no enforcing harness for '{alias}' raises ReadOnlyRoutingError ({exc!r})")

    runner = MagicMock()
    res, err, exc = _capture_stderr(
        dispatch, "rev", "Review PR #1", "auto", 300.0, False, None, env_agy, which_only_agy, runner,
    )
    check(exc is None and res["exit_code"] != 0 and res["success"] is False,
          f"dispatch fails closed with non-zero exit (got {res and res['exit_code']})")
    check(runner.call_count == 0, "no subprocess launched when failing closed")
    check("#323" in err, "fail-closed error on stderr cites #323")

    # main() (the CLI entrypoint) returns non-zero too.
    old_env = dict(os.environ)
    old_path = os.environ.get("PATH", "")
    with tempfile.TemporaryDirectory() as bindir:
        fake_agy = Path(bindir) / "agy"
        fake_agy.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_agy.chmod(0o755)
        try:
            for k in ("CLAUDE_PLUGIN_ROOT", "CLAUDE_PROJECT_DIR", "COPILOT_PLUGIN_DATA",
                      "COPILOT_PROJECT_DIR"):
                os.environ.pop(k, None)
            os.environ["ANTIGRAVITY_CONVERSATION_ID"] = "conv-123"
            os.environ["PATH"] = bindir
            code, err, exc = _capture_stderr(
                dispatch_main, ["--role", "research", "--prompt", "x", "--dry-run",
                                "--team-toml", os.path.join(bindir, "missing.toml")],
            )
        finally:
            os.environ.clear()
            os.environ.update(old_env)
            os.environ["PATH"] = old_path
    check(exc is None and code == 2, f"CLI main() exits 2 when no enforcing harness (got {code}, {exc!r})")

    # 6. Non-read-only role on agy: still routed there, with a one-line #323 warning.
    (h, fb, _), err, exc = _capture_stderr(
        resolve_target_harness, "antigravity", "sre", "auto", None, None, always_which,
    )
    check(exc is None and h == "antigravity" and not fb, f"non-read-only 'sre' on agy host stays on agy (got {h})")
    warn_lines = [ln for ln in err.splitlines() if "#323" in ln]
    check(len(warn_lines) == 1 and "does not apply mARC agent definitions" in warn_lines[0],
          f"non-read-only on agy emits one #323 warning line ({err.strip()!r})")

    # Same one-line warning for a non-read-only role on copilot.
    (h, fb, _), err, exc = _capture_stderr(
        resolve_target_harness, "copilot", "design", "auto", None, None, always_which,
    )
    warn_lines = [ln for ln in err.splitlines() if "#323" in ln]
    check(exc is None and h == "copilot" and len(warn_lines) == 1
          and "'copilot' headless dispatch does not apply mARC agent definitions" in warn_lines[0],
          f"non-read-only on copilot emits one #323 warning line ({err.strip()!r})")

    # And no such warning when nothing goes to agy.
    _, err, _ = _capture_stderr(resolve_target_harness, "claude-code", "dev", "auto", None, None, always_which)
    check("#323" not in err, "no #323 warning for claude-code dispatch")


# Literal, not imported: a drift in the shipped profile must fail this test (#351).
_CODEX_RO_PROFILE = [
    "-c", 'default_permissions="marc_readonly"',
    "-c", 'permissions.marc_readonly.filesystem={":root"="read"}',
    "-c", "permissions.marc_readonly.network.enabled=true",
]
_CODEX_FORBIDDEN_FLAGS = ("--dangerously-bypass-approvals-and-sandbox", "--full-auto",
                          "workspace-write", "danger-full-access", "--worktree", "--sandbox",
                          "--approve-for-me", "--add-dir")


def test_codex_readonly_opt_in() -> None:
    print("\n--- Test: opt-in Codex read-only reviewers (#351) ---")
    prompt = "Review PR #42"
    ro = ["sec", "security", "rev", "review", "research", "bulk-reader"]

    # 1. Default (no opt-in): every read-only role still refuses on codex.
    for r in ro:
        check(is_forbidden_readonly_route(r, "codex"), f"'{r}' on codex forbidden without opt-in")
        try:
            build_harness_command("codex", r, prompt)
            check(False, f"codex command for read-only '{r}' without opt-in should raise")
        except ReadOnlyRoutingError:
            check(True, f"codex command for read-only '{r}' without opt-in raises")
    # bulk-reader never opts in; the opt-in never unlocks another non-enforcing harness.
    check(is_forbidden_readonly_route("bulk-reader", "codex", True), "bulk-reader on codex forbidden even with opt-in")
    for h in ("antigravity", "copilot"):
        check(is_forbidden_readonly_route("rev", h, True), f"opt-in does not unlock '{h}'")
    try:
        build_harness_command("codex", "bulk-reader", prompt, codex_opt_in=True)
        check(False, "bulk-reader codex command with opt-in should raise")
    except ReadOnlyRoutingError:
        check(True, "bulk-reader codex command with opt-in raises")

    # 2. Opt-in: exact argv. -C <checkout>, fixed profile, effort pin from the
    #    Codex descriptor, persona-first prompt. No forbidden flag anywhere.
    # Pins come from the Codex agent descriptors: `core/agent-configs/codex`,
    # mirrored into every compiled plugin's `agent-configs/` (#385), plus the
    # Codex plugin's native `agents/*.toml`.
    plugin = Path(HERE).parent
    has_descriptors = (plugin / "agent-configs" / "codex").is_dir() or (plugin / "agents" / "review.toml").is_file()
    efforts = {"rev": "xhigh", "review": "xhigh", "sec": "xhigh", "security": "xhigh", "research": "medium"}
    with tempfile.TemporaryDirectory() as tmpdir:
        checkout = str(Path(tmpdir).resolve())
        for r, effort in efforts.items():
            agent = ROLE_TO_AGENT[r]
            body, pins = load_codex_persona(agent)
            want = {"model": "gpt-6.1-sol", "model_reasoning_effort": effort} if has_descriptors else {}
            check(pins == want, f"codex pins for '{r}': {pins}")
            pin_args = (["-m", "gpt-6.1-sol", "-c", f'model_reasoning_effort="{effort}"']
                        if has_descriptors else [])
            cmd = build_harness_command("codex", r, prompt, codex_opt_in=True, cwd=tmpdir)
            expected = (["codex", "exec", "-C", checkout] + _CODEX_RO_PROFILE + pin_args
                        + [build_codex_readonly_prompt(r, agent, body, prompt)])
            check(cmd == expected, f"opt-in codex argv for '{r}': {cmd[:12]}")
            check(not any(f in a for a in cmd[:-1] for f in _CODEX_FORBIDDEN_FLAGS),
                  f"opt-in codex argv for '{r}' carries no bypass/write flag")
            p = cmd[-1]
            check(f'<agent-definition name="{agent}">\n' in p and body in p,
                  f"persona body for '{r}' prepended to the prompt")
            check(p.index("<agent-definition") < p.index("Task:\n"), f"persona precedes the task for '{r}'")
            check(p.endswith(f"Task:\n{prompt}"), f"task prompt last for '{r}'")
            check("\nname: " not in body and not body.startswith("---"), f"front matter stripped for '{r}'")
            has_verdict = "make the LAST line of your final response the verdict line" in p
            check(has_verdict == (CANONICAL_ROLES[r] in ("rev", "sec")),
                  f"verdict-last-line contract for '{r}': {has_verdict}")

    # 3. A missing persona fails closed.
    with tempfile.TemporaryDirectory() as tmpdir:
        try:
            load_codex_persona("review", plugin_dir=Path(tmpdir))
            check(False, "missing persona should raise")
        except ReadOnlyRoutingError:
            check(True, "missing persona raises ReadOnlyRoutingError")

    # 4. codex_readonly_opt_in only accepts the literal "codex" route.
    check(codex_readonly_opt_in("sec", {"sec": "codex"}), "routes sec=codex opts in")
    check(codex_readonly_opt_in("@REV", {"rev": "codex"}), "normalized @REV opts in")
    for routes in ({}, None, {"sec": "auto"}, {"sec": "native"}, {"sec": "claude-code"}, {"rev": "codex"}):
        check(not codex_readonly_opt_in("sec", routes), f"no opt-in for sec with routes {routes}")
    check(not codex_readonly_opt_in("bulk-reader", {"bulk-reader": "codex"}), "bulk-reader never opts in")
    check(not codex_readonly_opt_in("dev", {"dev": "codex"}), "writer roles are not read-only opt-ins")

    # 5. Resolution: only the explicit team.toml route opts in.
    env_cc = {"CLAUDE_PROJECT_DIR": "/x"}
    env_codex = {"CODEX_PROJECT_DIR": "/x"}

    def all_which(b):
        return f"/bin/{b}"

    def no_codex(b):
        return None if b == "codex" else f"/bin/{b}"

    def only_codex(b):
        return "/bin/codex" if b == "codex" else None

    def res(role, harness, toml_text, env, which):
        """Returns (route_dict_or_None, exception_or_None)."""
        with tempfile.TemporaryDirectory() as d:
            t = None
            if toml_text is not None:
                t = Path(d) / "team.toml"
                t.write_text(toml_text, encoding="utf-8")
            out, _err, exc = _capture_stderr(resolve_route_detailed, role, harness, t, env, which)
            return out or {}, exc

    opt = '[orchestration.routes]\nsec = "codex"\nrev = "codex"\nresearch = "codex"\nbulk-reader = "codex"\n'
    for env in (env_cc, env_codex):
        for r in ("sec", "rev", "research"):
            d, _ = res(r, "auto", opt, env, all_which)
            check(d.get("harness") == "codex" and d.get("codex_readonly_opt_in") is True
                  and not d.get("policy_reroute"),
                  f"team.toml route codex opts '{r}' in (host {env}): {d.get('harness')}")
            d, _ = res(r, "codex", opt, env, all_which)
            check(d.get("harness") == "codex" and d.get("codex_readonly_opt_in") is True,
                  f"--harness codex + team.toml route opts '{r}' in: {d.get('harness')}")
        d, _ = res("bulk-reader", "auto", opt, env, all_which)
        check(d.get("harness") == "claude-code" and d.get("policy_reroute"), "bulk-reader route codex re-routed")
    for r in ("sec", "rev", "research"):
        # --harness codex alone is NOT an opt-in.
        for toml_text in (None, f'[orchestration.routes]\n{r} = "auto"\n'):
            d, _ = res(r, "codex", toml_text, env_cc, all_which)
            check(d.get("harness") == "claude-code" and d.get("policy_reroute")
                  and not d.get("codex_readonly_opt_in"),
                  f"--harness codex without team.toml opt-in re-routes '{r}': {d.get('harness')}")
        # auto / host detection / matrix / native on a codex host never pick codex.
        cases = [
            ("auto", None, "codex host matrix"),
            ("auto", f'[orchestration.routes]\n{r} = "native"\n', "route native on codex host"),
            ("auto", f'[orchestration]\nmode = "native"\n[orchestration.routes]\n{r} = "codex"\n',
             "native mode on codex host"),
            ("native", opt, "--harness native on codex host"),
            ("claude-code", opt, "explicit --harness claude-code"),
        ]
        for harness, toml_text, label in cases:
            d, _ = res(r, harness, toml_text, env_codex, all_which)
            check(d.get("harness") == "claude-code", f"{label} keeps '{r}' on claude-code: {d.get('harness')}")
        # Fallbacks never pick codex: codex missing -> claude-code; nothing enforcing -> refuse.
        d, _ = res(r, "auto", opt, env_codex, no_codex)
        check(d.get("harness") == "claude-code" and d.get("cli_fallback") and not d.get("codex_readonly_opt_in"),
              f"opted-in '{r}' with codex missing falls back to claude-code")
        d, exc = res(r, "auto", None, env_codex, only_codex)
        check(isinstance(exc, ReadOnlyRoutingError),
              f"'{r}' with only codex available and no opt-in refuses: {exc!r}")

    # 6. prepare_dispatch + the `start` job-spec re-check (#352 SEC-1) re-derive
    #    the opt-in from team.toml, never from the spec's own field.
    with tempfile.TemporaryDirectory() as d:
        t = Path(d) / "team.toml"
        t.write_text(opt, encoding="utf-8")
        prepared, _err, exc = _capture_stderr(prepare_dispatch, "rev", prompt, "auto", False, str(t),
                                              env_cc, all_which, Path(d))
        check(exc is None and prepared["command"][1:4] == ["exec", "-C", str(Path(d).resolve())],
              f"prepared opt-in command: {(prepared or {}).get('command', [])[:4]} {exc!r}")
        spec = {"prompt": prompt, "cwd": str(Path(d).resolve()), "prepared": prepared}
        _r, _e, exc = _capture_stderr(_validate_spec, spec)
        check(exc is None, f"job spec re-check accepts the opted-in codex reviewer: {exc!r}")
        prepared["codex_readonly_opt_in"] = True  # a forged flag must not matter
        t.write_text('[orchestration.routes]\nrev = "auto"\n', encoding="utf-8")
        _r, _e, exc = _capture_stderr(_validate_spec, spec)
        check(isinstance(exc, ReadOnlyRoutingError), f"re-check refuses once team.toml drops the opt-in: {exc!r}")
        # REV-2: forged native mode + codex. The re-check re-reads `mode` too.
        t.write_text('[orchestration]\nmode = "native"\n[orchestration.routes]\nrev = "codex"\n',
                     encoding="utf-8")
        _r, _e, exc = _capture_stderr(_validate_spec, spec)
        check(isinstance(exc, ReadOnlyRoutingError), f"re-check refuses native mode + codex spec: {exc!r}")
        # REV-2: the spec's recorded team_toml path is not trusted; only the
        # team.toml discovered from the spec cwd counts.
        with tempfile.TemporaryDirectory() as other:
            forged = Path(other) / "team.toml"
            forged.write_text(opt, encoding="utf-8")
            t.write_text('[orchestration.routes]\nrev = "auto"\n', encoding="utf-8")
            prepared["team_toml"] = str(forged)
            _r, _e, exc = _capture_stderr(_validate_spec, spec)
            check(isinstance(exc, ReadOnlyRoutingError),
                  f"re-check ignores a spec team_toml outside the checkout: {exc!r}")
            # REV-7: one rule for sync dispatch and `start` (both go through
            # prepare_dispatch): an opt-in counts only from the team.toml
            # discovered from the checkout. Any other --team-toml is refused,
            # outside the checkout or inside it.
            empty = Path(other) / "checkout"
            empty.mkdir()
            alt = Path(d) / "alt.toml"
            alt.write_text(opt, encoding="utf-8")
            for label, toml_arg, where in (("outside", str(forged), empty),
                                           ("inside (alt.toml)", str(alt), Path(d))):
                for dry in (True, False):
                    pre, _e, exc = _capture_stderr(prepare_dispatch, "rev", prompt, "auto", dry, toml_arg,
                                                   env_cc, all_which, where)
                    check(exc is None and pre.get("exit_code") == 2 and pre.get("command") == []
                          and pre.get("harness") is None,
                          f"--team-toml {label} opt-in refused (dry_run={dry}): {pre.get('exit_code')} {exc!r}")
                # dispatch() takes no cwd: run it from `where`, so "inside" really
                # is inside the checkout (REV-9).
                runner = MagicMock()
                prev_cwd = os.getcwd()
                os.chdir(str(where))
                try:
                    res_d, _e, exc = _capture_stderr(dispatch, "rev", prompt, "auto", 30.0, False, toml_arg,
                                                     env_cc, all_which, runner)
                finally:
                    os.chdir(prev_cwd)
                check(res_d is not None and res_d["exit_code"] == 2 and not runner.called,
                      f"sync dispatch refuses a --team-toml {label} opt-in without running anything")
            # The discovered team.toml passed explicitly is the same file: allowed.
            t.write_text(opt, encoding="utf-8")
            pre, _e, exc = _capture_stderr(prepare_dispatch, "rev", prompt, "auto", True, str(t),
                                           env_cc, all_which, Path(d))
            check(exc is None and pre.get("harness") == "codex",
                  f"--team-toml naming the discovered file keeps the opt-in: {pre.get('harness')} {exc!r}")
            # REV-9 positive: sync dispatch() from a checkout whose own team.toml
            # opts in runs the fixed Codex reviewer argv.
            runner = MagicMock(return_value=subprocess.CompletedProcess([], 0, "verdict: PASS\n", ""))
            prev_cwd = os.getcwd()
            os.chdir(d)
            try:
                res_d, _e, exc = _capture_stderr(dispatch, "rev", prompt, "auto", 30.0, False, None,
                                                 env_cc, all_which, runner)
            finally:
                os.chdir(prev_cwd)
            ran = runner.call_args[0][0] if runner.called else []
            check(exc is None and res_d["exit_code"] == 0 and res_d["harness"] == "codex"
                  and ran[1:4] == ["exec", "-C", str(Path(d).resolve())]
                  and ran[4:10] == _CODEX_RO_PROFILE,
                  f"sync dispatch from an opted-in checkout runs codex: {ran[:4]} {exc!r}")

    # REV-3: prepare_dispatch discovers team.toml from `cwd`, not the process cwd.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "team.toml").write_text(opt, encoding="utf-8")
        prepared, _err, exc = _capture_stderr(prepare_dispatch, "rev", prompt, "auto", True, None,
                                              env_cc, all_which, Path(d))
        check(exc is None and prepared.get("harness") == "codex"
              and prepared["command"][3] == str(Path(d).resolve()),
              f"team.toml discovered from cwd={d}, not {os.getcwd()}: {prepared.get('harness')} {exc!r}")

    # SEC-3: the effort pin is allowlisted before it reaches the `-c` TOML value.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "agents").mkdir()
        (Path(d) / "agents" / "review.md").write_text("---\nname: review\n---\nbody\n", encoding="utf-8")
        for bad in ('high" -c sandbox_mode="danger-full-access', "turbo", ""):
            (Path(d) / "agents" / "review.toml").write_text(
                f"model_reasoning_effort = '{bad}'\n", encoding="utf-8")
            try:
                body, pins = load_codex_persona("review", plugin_dir=Path(d))
                check(bad == "" and pins == {}, f"effort {bad!r} rejected or ignored: {pins}")
            except ReadOnlyRoutingError:
                check(bad != "", f"effort {bad!r} rejected")
        for good in ("minimal", "low", "medium", "high", "xhigh", "max"):
            (Path(d) / "agents" / "review.toml").write_text(
                f'model_reasoning_effort = "{good}"\n', encoding="utf-8")
            check(load_codex_persona("review", plugin_dir=Path(d))[1] == {"model_reasoning_effort": good},
                  f"effort {good!r} accepted")
        (Path(d) / "agents" / "review.toml").write_text('model = "--full-auto"\n', encoding="utf-8")
        try:
            load_codex_persona("review", plugin_dir=Path(d))
            check(False, "a model pin that parses as a flag should raise")
        except ReadOnlyRoutingError:
            check(True, "a model pin that parses as a flag raises")


def test_role_normalization_fail_closed() -> None:
    """Regression for #323 review: near-miss role spellings can't bypass the guard."""
    print("\n--- Test: role normalization and unknown-role rejection (#323) ---")
    always_which = lambda b: f"/usr/bin/{b}"
    env_agy = {"ANTIGRAVITY_CONVERSATION_ID": "conv-123"}

    cases = {"Rev": "rev", "REV": "rev", " rev": "rev", "@rev": "rev", "rev ": "rev",
             "Research": "research", "REVIEW": "review", "@Security": "security"}
    for raw, expected in cases.items():
        check(normalize_role(raw) == expected, f"normalize_role({raw!r}) == {expected!r}")

    # On an agy host (where sre/design would stay on agy), every spelling of a
    # read-only role still resolves to claude-code, both auto and explicit agy.
    for raw in ("Rev", "REV", " rev", "@rev", "Research", "REVIEW"):
        for requested in ("auto", "antigravity"):
            (h, _, _), _, exc = _capture_stderr(
                resolve_route, raw, requested, None, env_agy, always_which,
            )
            check(exc is None and h == "claude-code",
                  f"role {raw!r} ({requested}) on agy host -> claude-code (got {h}, {exc!r})")
        res, _, exc = _capture_stderr(
            dispatch, raw, "x", "antigravity", 300.0, True, None, env_agy, always_which,
        )
        check(exc is None and res["command"][:1] == ["claude"],
              f"dispatch({raw!r}, --harness antigravity) builds a claude command")

    # Unknown roles: rejected, exit 2, no subprocess started.
    for raw in ("reviewer", "foo", "", "@", "re v"):
        _, _, exc = _capture_stderr(resolve_target_harness, "claude-code", raw, "auto", None, None, always_which)
        check(isinstance(exc, UnknownRoleError), f"resolve_target_harness rejects unknown role {raw!r}")
        runner = MagicMock()
        res, err, exc = _capture_stderr(
            dispatch, raw, "x", "auto", 300.0, False, None, env_agy, always_which, runner,
        )
        check(exc is None and res["exit_code"] == 2 and res["success"] is False and res["command"] == [],
              f"dispatch rejects unknown role {raw!r} with exit 2 (got {res and res['exit_code']})")
        check(runner.call_count == 0, f"no subprocess for unknown role {raw!r}")
        check("unknown role" in err, f"stderr names the unknown role {raw!r}")

    code, err, exc = _capture_stderr(
        dispatch_main, ["--role", "reviewer", "--prompt", "x", "--dry-run", "--harness", "claude-code"],
    )
    check(exc is None and code == 2, f"CLI main() exits 2 for unknown role (got {code}, {exc!r})")


SCRIPT = os.path.join(HERE, "dispatch_agent.py")

# A real, short-lived stub harness CLI (#352): the detached runner execs it, so
# detachment is exercised for real. Behavior comes from the prompt (last argv):
# "sleep=<s> exit=<n> pidfile=<path> marker=<path>".
_STUB_HARNESS = """\
import os, sys, time
opts = dict(t.split("=", 1) for t in sys.argv[-1].split() if "=" in t)
for key in ("pidfile", "marker"):
    if key in opts:
        with open(opts[key], "w") as f:
            f.write(str(os.getpid()))
time.sleep(float(opts.get("sleep", "0")))
print("stub-report: done")
sys.exit(int(opts.get("exit", "0")))
"""


def _make_stub_bin(bin_dir: Path, names: List[str]) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    git = shutil.which("git")
    if git:
        os.symlink(git, bin_dir / "git")
    for name in names:
        p = bin_dir / name
        p.write_text(f"#!{sys.executable}\n" + _STUB_HARNESS, encoding="utf-8")
        p.chmod(0o755)


def _job_env(bin_dir: Path) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("CLAUDE_", "COPILOT_", "AGY_", "ANTIGRAVITY_", "CODEX_"))
           and k not in ("PLUGIN_ROOT", "XDG_STATE_HOME")}
    env["PATH"] = str(bin_dir)
    return env


def _cli(args: List[str], cwd: Path, env: Dict[str, str], timeout: float = 90) -> "subprocess.CompletedProcess[str]":
    return subprocess.run([sys.executable, SCRIPT] + args, cwd=str(cwd), env=env,
                          capture_output=True, text=True, timeout=timeout)


def _lines(out: str) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in out.splitlines() if line.strip()]


def _kill_group(pid: int) -> None:
    import signal
    try:
        os.killpg(pid, signal.SIGKILL)
    except OSError:
        pass


def _pid_gone(pid: int, within: float) -> bool:
    import time as _time
    deadline = _time.monotonic() + within
    while _time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except OSError:
            return True
        stat = Path(f"/proc/{pid}/stat")
        try:
            if stat.read_text().rsplit(")", 1)[-1].split()[0] == "Z":
                return True
        except (OSError, IndexError):
            pass
        _time.sleep(0.1)
    return False


def test_start_wait_jobs() -> None:
    """start/wait detached jobs with a real stub subprocess (#352)."""
    import signal
    import time as _time
    started_pids: List[int] = []
    harness_pids: List[int] = []
    with tempfile.TemporaryDirectory() as tmp:
        try:
            _start_wait_cases(Path(tmp), started_pids, harness_pids, signal, _time)
        finally:
            # Always reap detached runners and stub harnesses, even when a check failed (REV-11).
            for pid in started_pids + harness_pids:
                _kill_group(pid)
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass


def _start_wait_cases(tmp_p: Path, started_pids: List[int], harness_pids: List[int],
                      signal: Any, _time: Any) -> None:
    repo = tmp_p / "repo"
    repo.mkdir()
    bin_dir = tmp_p / "bin"
    _make_stub_bin(bin_dir, ["claude", "copilot"])
    env = _job_env(bin_dir)
    subprocess.run(["git", "init", "-q", str(repo)], check=True, env=env)
    common = Path(subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-common-dir"],
                                 capture_output=True, text=True, env=env).stdout.strip())
    jobs = (repo / common if not common.is_absolute() else common).resolve() / "marc" / "jobs"

    def start(prompt: str, *extra: str, role: str = "dev") -> Dict[str, Any]:
        r = _cli(["start", "--role", role, "--harness", "claude-code", *extra, "--prompt", prompt], repo, env)
        out = _lines(r.stdout)
        if out and "pid" in out[0]:
            started_pids.append(out[0]["pid"])
        check(r.returncode == 0 and len(out) == 1, f"start {prompt!r} exits 0 (got {r.returncode}, {r.stderr!r})")
        return out[0] if out else {"job_id": "missing", "pid": 0, "result_path": "/nonexistent"}

    # start returns before the child finishes, with a compact JSON line.
    t0 = _time.monotonic()
    job = start("sleep=8 exit=0")
    elapsed = _time.monotonic() - t0
    check(set(job) == {"job_id", "pid", "role", "harness", "result_path"},
          f"start prints one compact JSON line with the 5 fields (got {job!r})")
    check(elapsed < 6, f"start returns well before the 8s child finishes ({elapsed:.2f}s)")
    check(not Path(job["result_path"]).exists(), "no result file yet right after start (child still running)")
    check(Path(job["result_path"]).parent.parent == jobs,
          "job state lives under $(git rev-parse --git-common-dir)/marc/jobs")
    check(not any(p.name.startswith(".tmp-") for p in jobs.iterdir()), "no staging dir left after start")

    # wait --timeout returns the still-running code with status running.
    r = _cli(["wait", job["job_id"], "--timeout", "0.5"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 75 and out and out[0]["status"] == "running",
          f"wait --timeout on a live job exits 75 with status running (got {r.returncode}, {r.stdout!r})")

    # Liveness does not depend on pid visibility (REV-1): with the stored pid
    # replaced by one that does not exist, the held lock still reads running.
    meta_path = jobs / job["job_id"] / "job.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["pid"] = 2 ** 22 + 4321
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
    r = _cli(["wait", job["job_id"], "--timeout", "0.5"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 75 and out and out[0]["status"] == "running",
          f"held lock reads running even when the pid cannot be looked up (got {r.returncode}, {r.stdout!r})")

    # --store lets a caller outside the clone find the job (REV-10).
    r = _cli(["wait", job["job_id"], "--timeout", "0.2", "--store", str(jobs)], tmp_p, env)
    check(r.returncode == 75, f"wait --store works from outside the repo (got {r.returncode}, {r.stderr!r})")

    # wait blocks until done and returns 0; output is compact.
    r = _cli(["wait", job["job_id"], "--timeout", "60"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 0 and out and out[0]["status"] == "succeeded" and out[0]["exit_code"] == 0,
          f"wait blocks until the job succeeds and exits 0 (got {r.returncode}, {r.stdout!r})")
    check(bool(out) and set(out[0]) == {"job_id", "status", "exit_code", "duration_sec", "result_path"},
          "wait summary carries only the compact fields")
    check("command" not in r.stdout and "stub-report" not in r.stdout,
          "wait never prints command/command_str or full stdout")
    full = json.loads(Path(job["result_path"]).read_text(encoding="utf-8"))
    check(full.get("success") is True and "stub-report: done" in full.get("stdout", "")
          and full.get("command", [None])[0] == "claude",
          "result file holds the full dispatch() result dict")
    check(not any(p.name.endswith(".tmp") for p in Path(job["result_path"]).parent.iterdir()),
          "atomic write leaves no temp file behind")

    # A failing child reports failed with its non-zero exit.
    fail_job = start("exit=3")
    r = _cli(["wait", fail_job["job_id"], "--timeout", "60"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 1 and out and out[0]["status"] == "failed" and out[0]["exit_code"] == 3,
          f"failing child reports failed with exit 3, wait exits 1 (got {r.returncode}, {r.stdout!r})")

    # Harness timeout reports timed_out.
    to_job = start("sleep=20", "--timeout", "0.5")
    r = _cli(["wait", to_job["job_id"], "--timeout", "60"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 1 and out and out[0]["status"] == "timed_out",
          f"harness timeout reports timed_out (got {r.returncode}, {r.stdout!r})")
    to_full = json.loads(Path(to_job["result_path"]).read_text(encoding="utf-8"))
    check(to_full.get("timed_out") is True, "timeout is a structured timed_out field, not an error string")

    # SIGKILL on the runner alone: no result -> lost (no hang), and the harness
    # child does not outlive it (REV-9, PR_SET_PDEATHSIG on Linux).
    pidfile = tmp_p / "lost.pid"
    lost_job = start(f"sleep=60 pidfile={pidfile}")
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        _time.sleep(0.1)
    harness_pid = int(pidfile.read_text() or 0) if pidfile.exists() else 0
    if harness_pid:
        harness_pids.append(harness_pid)
    os.kill(lost_job["pid"], signal.SIGKILL)
    t0 = _time.monotonic()
    r = _cli(["wait", lost_job["job_id"], "--timeout", "30"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 1 and out and out[0]["status"] == "lost",
          f"killed runner with no result reports lost (got {r.returncode}, {r.stdout!r})")
    check(_time.monotonic() - t0 < 20, "lost detection does not wait out the timeout")
    if sys.platform.startswith("linux"):
        check(harness_pid > 0 and _pid_gone(harness_pid, 10),
              "harness child dies with a SIGKILLed runner (PDEATHSIG)")

    # SIGTERM on the runner: harness group killed, result written as failed.
    pidfile = tmp_p / "term.pid"
    term_job = start(f"sleep=60 pidfile={pidfile}")
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        _time.sleep(0.1)
    harness_pid = int(pidfile.read_text() or 0) if pidfile.exists() else 0
    if harness_pid:
        harness_pids.append(harness_pid)
    os.kill(term_job["pid"], signal.SIGTERM)
    r = _cli(["wait", term_job["job_id"], "--timeout", "30"], repo, env)
    out = _lines(r.stdout)
    check(r.returncode == 1 and out and out[0]["status"] == "failed",
          f"SIGTERMed runner records a failed result (got {r.returncode}, {r.stdout!r})")
    check(harness_pid > 0 and _pid_gone(harness_pid, 10), "runner kills the harness group on SIGTERM")

    # --any over two jobs returns when the first finishes.
    fast = start("sleep=0.2")
    slow = start("sleep=60")
    r = _cli(["wait", fast["job_id"], slow["job_id"], "--any", "--timeout", "60"], repo, env)
    out = {s["job_id"]: s for s in _lines(r.stdout)}
    check(r.returncode == 0 and out.get(fast["job_id"], {}).get("status") == "succeeded"
          and out.get(slow["job_id"], {}).get("status") == "running",
          f"--any returns 0 once one job succeeds, the other still running (got {r.returncode}, {r.stdout!r})")
    _kill_group(slow["pid"])

    # Unknown / malformed job ids are a usage error.
    r = _cli(["wait", "../../etc", "--timeout", "0"], repo, env)
    check(r.returncode == 2, f"wait rejects a malformed job id with exit 2 (got {r.returncode})")

    # SEC-1: the runner re-applies the #323 guard and the routed command
    # before exec, and only accepts job dirs inside the store.
    marker = tmp_p / "tampered.marker"
    for name, job_id, prepared in (
        ("read-only role on copilot", "20000101-000000-aaaaaaaa",
         {"role": "rev", "harness": "copilot", "command": ["copilot", "--prompt", f"marker={marker}"]}),
        ("command not the routed one", "20000101-000000-bbbbbbbb",
         {"role": "dev", "harness": "claude-code", "command": ["claude", "-p", f"marker={marker}"]}),
    ):
        jdir = jobs / job_id
        jdir.mkdir()
        (jdir / "lock").write_text("")
        (jdir / "spec.json").write_text(json.dumps({"job_id": job_id, "timeout": 30,
                                                     "prompt": f"marker={marker}", "prepared": prepared}))
        r = _cli(["_run", str(jdir)], repo, env)
        res = json.loads((jdir / "result.json").read_text()) if (jdir / "result.json").exists() else {}
        check(r.returncode == 0 and res.get("status") == "failed" and not marker.exists(),
              f"runner refuses a tampered spec ({name}) without executing it (got {res.get('error')!r})")
    outside = tmp_p / "outside-job"
    outside.mkdir()
    r = _cli(["_run", str(outside)], repo, env)
    check(r.returncode == 2 and "not inside the job store" in r.stderr,
          f"runner refuses a job dir outside the store (got {r.returncode}, {r.stderr!r})")

    # Read-only roles routed to any forbidden harness fail closed, on both the
    # synchronous and the start path (#323), through one shared predicate.
    import dispatch_agent as _da
    for h in sorted(NON_ENFORCING_HARNESSES):
        for role in ("rev", "sec", "research", "bulk-reader"):
            check(_da.is_forbidden_readonly_route(role, h), f"{role} on {h} is a forbidden route")
    check(not _da.is_forbidden_readonly_route("dev", "copilot"), "writer on copilot is allowed")
    check(_da.is_forbidden_readonly_route("bulk-reader", "native"), "bulk-reader off claude-code is forbidden")
    check(not _da.is_forbidden_readonly_route("rev", "claude-code"), "read-only on claude-code is allowed")
    ro_bin = tmp_p / "ro-bin"
    _make_stub_bin(ro_bin, ["codex", "copilot", "agy"])  # no `claude` on PATH
    before = sorted(p.name for p in jobs.iterdir())
    for h in ("antigravity", "copilot", "codex"):
        for role in ("rev", "bulk-reader"):
            r = _cli(["start", "--role", role, "--harness", h, "--prompt", "x"], repo, _job_env(ro_bin))
            check(r.returncode == 2 and r.stdout.strip() == "" and "#323" in r.stderr,
                  f"start: read-only {role} to {h} without claude fails closed (got {r.returncode}, {r.stderr!r})")
            r = _cli(["--role", role, "--harness", h, "--prompt", "x", "--json"], repo, _job_env(ro_bin))
            res = json.loads(r.stdout) if r.stdout.strip() else {}
            check(r.returncode == 2 and res.get("command") == [] and res.get("harness") is None,
                  f"sync: read-only {role} to {h} without claude fails closed (got {r.returncode})")
    check(sorted(p.name for p in jobs.iterdir()) == before, "a refused start creates no job")

    # An unwritable job store is a clean exit 2, not a traceback (REV-1).
    if os.geteuid() != 0:
        jobs.chmod(0o500)
        try:
            r = _cli(["start", "--role", "dev", "--harness", "claude-code", "--prompt", "x"], repo, env)
        finally:
            jobs.chmod(0o700)
        check(r.returncode == 2 and "Traceback" not in r.stderr and "[mARC dispatch] Error" in r.stderr,
              f"unwritable job store exits 2 with a one-line error (got {r.returncode}, {r.stderr!r})")

    # SEC-7: a FIFO planted at `lock` cannot hang wait; it reads as lost.
    fifo_id = "20000101-000000-cccccccc"
    (jobs / fifo_id).mkdir()
    os.mkfifo(str(jobs / fifo_id / "lock"))
    t0 = _time.monotonic()
    try:
        r = _cli(["wait", fifo_id, "--timeout", "2"], repo, env, timeout=20)
        out = _lines(r.stdout)
        check(r.returncode == 1 and out and out[0]["status"] == "lost" and _time.monotonic() - t0 < 15,
              f"FIFO at lock reads lost within --timeout (got {r.returncode}, {r.stdout!r})")
    except subprocess.TimeoutExpired:
        check(False, "wait hung on a FIFO at lock")

    # A start that fails before publishing leaves no `.tmp-<id>` staging dir.
    saved_write, saved_path = _da._write_json_atomic, os.environ.get("PATH", "")
    before = sorted(p.name for p in jobs.iterdir())
    raised = None

    def _failing_write(path: Path, data: Dict[str, Any]) -> None:
        raise OSError(28, "No space left on device")
    try:
        os.environ["PATH"] = str(bin_dir)
        _da._write_json_atomic = _failing_write
        try:
            _da.start_job("dev", "x", "claude-code", cwd=repo)
        except _da.JobStoreError as e:
            raised = e
    finally:
        _da._write_json_atomic = saved_write
        os.environ["PATH"] = saved_path
    check(raised is not None and "cannot create job" in str(raised)
          and sorted(p.name for p in jobs.iterdir()) == before,
          f"failed staging write removes the staging dir (got {raised!r})")

    # Termination signals outside the harness run are deferred, not raised,
    # so the final result.json write cannot be interrupted (#352 review).
    saved_pgid, saved_pending = _da._HARNESS_PGID, _da._TERM_PENDING
    try:
        _da._HARNESS_PGID = None
        try:
            _da._on_term_signal(15, None)
            check(_da._TERM_PENDING == 15, "signal outside the harness run is recorded, not raised")
        except _da._RunnerTerminated:
            check(False, "signal outside the harness run must not raise")
        _da._HARNESS_PGID = 999999
        try:
            _da._on_term_signal(15, None)
            check(False, "signal during the harness run should raise")
        except _da._RunnerTerminated:
            check(True, "signal during the harness run interrupts it")
    finally:
        _da._HARNESS_PGID, _da._TERM_PENDING = saved_pgid, saved_pending

    # wait parses a finished job's result once, not on every poll.
    calls: Dict[str, int] = {}
    saved_summary = _da.job_summary

    def _counting_summary(job_id: str, root: Path) -> Dict[str, Any]:
        calls[job_id] = calls.get(job_id, 0) + 1
        return saved_summary(job_id, root)
    slow2 = start("sleep=60")
    try:
        _da.job_summary = _counting_summary
        code, _ = _da.wait_jobs([job["job_id"], slow2["job_id"]], timeout=1.5, root=jobs)
    finally:
        _da.job_summary = saved_summary
        _kill_group(slow2["pid"])
    check(code == 75 and calls.get(job["job_id"]) == 1 and calls.get(slow2["job_id"], 0) > 1,
          f"finished summaries are cached across polls (calls={calls})")

    # A start that fails after publishing the job leaves nothing behind.
    saved_exe, saved_path = sys.executable, os.environ.get("PATH", "")
    before = sorted(p.name for p in jobs.iterdir())
    raised = None
    try:
        os.environ["PATH"] = str(bin_dir)
        _da.sys.executable = str(tmp_p / "no-such-python")  # Popen of the runner fails
        try:
            _da.start_job("dev", "x", "claude-code", cwd=repo)
        except _da.JobStoreError as e:
            raised = e
    finally:
        _da.sys.executable = saved_exe
        os.environ["PATH"] = saved_path
    check(raised is not None and "failed to spawn runner" in str(raised)
          and sorted(p.name for p in jobs.iterdir()) == before,
          f"failed spawn says so and removes the published job (got {raised!r})")

    # Backward-compatible no-subcommand invocation still runs synchronously.
    r = _cli(["--role", "dev", "--harness", "claude-code", "--prompt", "exit=0", "--json"], repo, env)
    check(r.returncode == 0 and json.loads(r.stdout).get("success") is True,
          "no-subcommand invocation behaves as before")

    status = subprocess.run(["git", "-C", str(repo), "status", "--porcelain"],
                            capture_output=True, text=True, env=env)
    check(status.returncode == 0 and status.stdout == "",
          f"git status stays clean after start/wait (got {status.stdout!r})")


def test_model_matrix_385() -> None:
    """#385: Codex/Antigravity model pins, consumer overrides, profile precedence."""
    print("\n--- Test: Codex/Antigravity model matrix (#385) ---")
    prompt = "Implement issue #1"
    plugin = Path(HERE).parent

    # 1. Every shipped Codex descriptor loads (the #351 allowlists accept the slugs).
    want_codex = {
        "engineer": ("gpt-6.1-sol", "high"), "sre": ("gpt-6.1-sol", "high"),
        "design": ("gpt-6.1-sol", "high"), "research": ("gpt-6.1-sol", "medium"),
        "review": ("gpt-6.1-sol", "xhigh"), "security": ("gpt-6.1-sol", "xhigh"),
        "bulk-reader": ("gpt-6-luna", "high"),
    }
    for agent, (model, effort) in want_codex.items():
        pins = load_codex_pins(agent)
        check(pins == {"model": model, "model_reasoning_effort": effort},
              f"shipped codex descriptor for '{agent}' loads: {pins}")
    shipped = sorted(p.stem for p in (plugin / "agent-configs" / "codex").glob("*.toml"))
    check(shipped == sorted(want_codex), f"codex descriptor set is the matrix: {shipped}")

    # 2. Every shipped Antigravity descriptor loads; only writer roles have one (#323).
    agy_dir = plugin / "agent-configs" / "antigravity"
    agy_shipped = sorted(p.stem for p in agy_dir.glob("*.toml"))
    check(agy_shipped == ["design", "sre"], f"antigravity descriptors are sre/design only: {agy_shipped}")
    for agent in agy_shipped:
        check(load_agy_model(agent) == "gemini-4-argon-high", f"antigravity descriptor for '{agent}' loads")
    check(load_agy_model("engineer") is None, "no antigravity descriptor for engineer -> no model")

    # 3. Antigravity argv: --model from the descriptor, nothing when absent, never --effort.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".git").mkdir()
        for r, model in (("sre", "gemini-4-argon-high"), ("design", "gemini-4-argon-high"), ("dev", None)):
            cmd = build_harness_command("antigravity", r, prompt, cwd=d)
            check(("--model" in cmd) == bool(model) and (not model or cmd[cmd.index("--model") + 1] == model),
                  f"agy argv for '{r}' carries --model {model}: {cmd[:5]}")
            check("--effort" not in cmd, f"agy argv for '{r}' never passes --effort")
            check(cmd.index("-p") == len(cmd) - 2, f"agy argv for '{r}' keeps -p <prompt> last")

        # 4. Writer pins on codex, no team.toml: persona model + effort.
        cmd = build_harness_command("codex", "dev", prompt, cwd=d)
        check(cmd[5:9] == ["-m", "gpt-6.1-sol", "-c", 'model_reasoning_effort="high"'],
              f"codex writer argv carries persona pins: {cmd[:9]}")

        # 5. Consumer overrides from the checkout's team.toml.
        team = Path(d) / ".agents"
        team.mkdir()
        (team / "team.toml").write_text(
            '[orchestration.codex_profiles]\ndev = "local"\nrev = "local"\n'
            '[orchestration.antigravity_models]\nsre = "claude-sonnet-5-5-high"\n', encoding="utf-8")
        cmd = build_harness_command("codex", "engineer", prompt, cwd=d)
        check(cmd == ["codex", "exec", "--worktree", "--sandbox", "workspace-write", "-p", "local",
                      f"Act as the mARC engineer specialist. {prompt}"],
              f"a codex profile replaces both persona pins (profile wins): {cmd}")
        cmd = build_harness_command("codex", "sre", prompt, cwd=d)
        check("-m" in cmd and "-p" not in cmd, f"a role without a profile keeps the persona pins: {cmd[:9]}")
        cmd = build_harness_command("antigravity", "sre", prompt, cwd=d)
        check(cmd[2:4] == ["--model", "claude-sonnet-5-5-high"], f"agy override wins over the descriptor: {cmd[:4]}")
        cmd = build_harness_command("antigravity", "design", prompt, cwd=d)
        check(cmd[2:4] == ["--model", "gemini-4-argon-high"], f"unoverridden agy role keeps its descriptor: {cmd[:4]}")
        # The #351 reviewer argv stays fixed: a codex profile never reaches it.
        cmd = build_harness_command("codex", "rev", prompt, codex_opt_in=True, cwd=d)
        check("-p" not in cmd[:-1] and "local" not in cmd[:-1], "codex reviewer argv ignores codex_profiles")

        # 6. The detached runner re-derives the same argv from the checkout (#352).
        prepared = prepare_dispatch("dev", prompt, harness="codex", dry_run=True,
                                    which_fn=lambda b: f"/usr/bin/{b}", cwd=Path(d))
        spec = {"prompt": prompt, "cwd": d, "prepared": prepared}
        check(_validate_spec(spec)["command"][5:7] == ["-p", "local"], "runner re-check accepts the profile argv")
        tampered = dict(prepared, command=prepared["command"][:5] + ["-p", "other"] + prepared["command"][7:])
        try:
            _validate_spec({"prompt": prompt, "cwd": d, "prepared": tampered})
            check(False, "a tampered profile in the job spec should be refused")
        except RuntimeError:
            check(True, "a tampered profile in the job spec is refused")

        # 7. Malformed overrides fail closed (exit 2, nothing built).
        for table, bad in (("codex_profiles", '"-c x"'), ("codex_profiles", "3"),
                           ("antigravity_models", '"--yolo"'), ("antigravity_models", '"Gem ini"')):
            role = "dev" if table == "codex_profiles" else "sre"
            harness = "codex" if table == "codex_profiles" else "antigravity"
            (team / "team.toml").write_text(f"[orchestration.{table}]\n{role} = {bad}\n", encoding="utf-8")
            try:
                build_harness_command(harness, role, prompt, cwd=d)
                check(False, f"{table} {bad} should raise")
            except ModelPinError:
                check(True, f"{table} {bad} raises ModelPinError")
            res, _, exc = _capture_stderr(prepare_dispatch, role, prompt, harness, True, None, None,
                                          lambda b: f"/usr/bin/{b}", Path(d))
            check(exc is None and res["exit_code"] == 2 and res["command"] == [],
                  f"prepare_dispatch refuses {table} {bad} with exit 2")

        # 7b. Only the target harness's table is validated (PR #387 REV-1): a bad
        #     codex_profiles.sre neither blocks an agy sre dispatch nor is named.
        (team / "team.toml").write_text(
            '[orchestration.codex_profiles]\nsre = "-c bad"\n'
            '[orchestration.antigravity_models]\ndesign = "--bad"\n', encoding="utf-8")
        res, err, exc = _capture_stderr(prepare_dispatch, "sre", prompt, "antigravity", True, None, None,
                                        lambda b: f"/usr/bin/{b}", Path(d))
        check(exc is None and res["exit_code"] == 0 and res["command"][2:4] == ["--model", "gemini-4-argon-high"]
              and "codex_profiles" not in err,
              f"bad codex_profiles.sre does not block agy sre: {res.get('exit_code')} {err!r}")
        res, err, exc = _capture_stderr(prepare_dispatch, "design", prompt, "codex", True, None, None,
                                        lambda b: f"/usr/bin/{b}", Path(d))
        check(exc is None and res["exit_code"] == 0 and "-m" in res["command"] and "antigravity_models" not in err,
              f"bad antigravity_models.design does not block codex design: {res.get('exit_code')} {err!r}")
        res, err, exc = _capture_stderr(prepare_dispatch, "sre", prompt, "codex", True, None, None,
                                        lambda b: f"/usr/bin/{b}", Path(d))
        check(exc is None and res["exit_code"] == 2 and "codex_profiles" in err,
              "bad codex_profiles.sre still fails closed for a codex sre dispatch")

    # 8. A malformed shipped descriptor fails closed too.
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "agent-configs" / "antigravity").mkdir(parents=True)
        for bad in ("--model x", "-high", "UPPER", "a b"):
            (Path(d) / "agent-configs" / "antigravity" / "sre.toml").write_text(f'model = "{bad}"\n', encoding="utf-8")
            try:
                load_agy_model("sre", plugin_dir=Path(d))
                check(False, f"descriptor slug {bad!r} should raise")
            except ModelPinError:
                check(True, f"descriptor slug {bad!r} raises ModelPinError")

    # 9. An unknown slug is surfaced, not masked: agy's non-zero exit is the
    #    result and there is no retry without --model.
    runner = MagicMock(return_value=subprocess.CompletedProcess(
        args=[], returncode=1, stdout="", stderr="ERROR: unknown model"))
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / ".git").mkdir()
        old = os.getcwd()
        os.chdir(d)
        try:
            res = dispatch("sre", prompt, harness="antigravity", which_fn=lambda b: f"/usr/bin/{b}",
                           runner_fn=runner)
        finally:
            os.chdir(old)
    check(res["exit_code"] == 1 and res["success"] is False and "unknown model" in res["stderr"],
          f"agy unknown-slug exit surfaces (exit {res['exit_code']})")
    check(runner.call_count == 1 and "--model" in runner.call_args[0][0], "no silent retry without --model")


def test_arch_role_388() -> None:
    """@arch is a read-only, claude-code-only role; its definition carries the marker (#388)."""
    print("\n[arch] read-only architect role (#388)")
    check("arch" in READ_ONLY_ROLES, "arch is in READ_ONLY_ROLES")
    check(ROLE_TO_AGENT.get("arch") == "architect" and ROLE_TO_AGENT.get("architect") == "architect",
          "arch/architect map to the 'architect' agent")
    check(CANONICAL_ROLES.get("architect") == "arch", "architect aliases to canonical arch")
    check("arch" not in CODEX_READONLY_ROLES, "arch is never a codex read-only opt-in role")
    for host in DEFAULT_HYBRID_MATRIX:
        for r in ("arch", "architect"):
            check(DEFAULT_HYBRID_MATRIX[host].get(r) == "claude-code",
                  f"matrix[{host}][{r}] routes to claude-code")
    env_cc = {"CLAUDE_PLUGIN_ROOT": "/plugin"}
    for h in ("antigravity", "copilot", "codex"):
        check(is_forbidden_readonly_route("arch", h), f"arch forbidden on {h}")
        check(is_forbidden_readonly_route("@Architect", h, True), f"@Architect forbidden on {h} even with codex opt-in flag")
        try:
            build_harness_command(h, "arch", "x")
            check(False, f"{h} command for arch should raise")
        except ReadOnlyRoutingError:
            check(True, f"{h} command for arch raises")
    check(not is_forbidden_readonly_route("arch", "claude-code"), "arch allowed on claude-code")
    check(build_harness_command("claude-code", "arch", "x")[:4]
          == ["claude", "--dangerously-skip-permissions", "--agent", "architect"],
          "claude-code command selects the architect agent")
    # Marker and read-only tool contract in the agent definition (mirrors the @sec/@rev markers).
    root = Path(HERE).parent
    agent = next((c for c in (root / "agents" / "architect.md",
                              root / "skills" / "architect" / "SKILL.md") if c.is_file()), None)
    check(agent is not None, "architect persona file found (agents/ or skills/architect/)")
    if agent is None:
        return
    text = agent.read_text(encoding="utf-8")
    check("## marc:arch decision" in text, "architect persona carries the '## marc:arch decision' marker")
    tools = next((l for l in text.splitlines() if l.startswith("tools:")), None)
    check(tools is not None,
          "architect persona declares a 'tools:' line (absent = inherits all tools, write access)")
    if tools is not None:
        check([t.strip() for t in tools[len("tools:"):].split(",")]
              == ["Read", "Grep", "Glob", "Bash"],
              f"architect tools are exactly Read, Grep, Glob, Bash ({tools})")
    check("model: opus" in text and "model: fable" not in text,
          "architect persona is pinned to opus (no fable default)")


class _IsoEnv:
    """Point HOME and XDG_CONFIG_HOME at a temp dir so the operator's real files never leak in."""

    def __init__(self, home: Path, xdg: Optional[Path] = None, codex_home: Optional[Path] = None) -> None:
        self.new = {"HOME": str(home), "XDG_CONFIG_HOME": str(xdg or home / ".config"),
                    "CODEX_HOME": str(codex_home or home / ".codex")}

    def __enter__(self) -> "_IsoEnv":
        self.old = {k: os.environ.get(k) for k in self.new}
        os.environ.update(self.new)
        return self

    def __exit__(self, *exc: Any) -> None:
        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _git_env_run(*a: str, cwd: Any) -> None:
    genv = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t", GIT_COMMITTER_NAME="t",
                GIT_COMMITTER_EMAIL="t@t")
    subprocess.run(["git", *a], cwd=cwd, env=genv, check=True, capture_output=True)


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True)
    _git_env_run("init", "-q", cwd=repo)
    (repo / "a.txt").write_text("one\n", encoding="utf-8")
    _git_env_run("add", "a.txt", cwd=repo)
    _git_env_run("commit", "-q", "-m", "init", cwd=repo)


def test_codex_writer_timeout_sandbox_partial_401() -> None:
    old_umask = os.umask(0o077)  # config files must not be group/world-writable (SEC-8)
    try:
        _test_codex_writer_401()
    finally:
        os.umask(old_umask)


def _test_codex_writer_401() -> None:
    print("\n--- Test: codex writer timeout, user-level sandbox widening, partial work (#401) ---")
    prompt = "Do the task"
    which = lambda b: f"/usr/bin/{b}"

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(os.path.realpath(tmp))
        home = root / "home"
        (home / ".config" / "marc").mkdir(parents=True)
        for sub in (".ssh", ".codex", "toolchain"):
            (home / sub).mkdir()
        (root / "cache" / "ok").mkdir(parents=True)
        repo = root / "work" / "repo"
        _init_repo(repo)
        cfg = home / ".config" / "marc" / "codex.toml"
        team = repo / ".agents"
        team.mkdir()
        top = str(repo)

        def prep(**kw: Any):
            return _capture_stderr(prepare_dispatch, "dev", prompt, harness="codex", dry_run=True,
                                   which_fn=which, cwd=repo, **kw)

        with _IsoEnv(home):
            # 1. Defaults: argv unchanged, 1800 s for writers, 300 s for everything else.
            cmd = build_harness_command("codex", "dev", prompt, cwd=str(repo))
            check("--add-dir" not in cmd and not any("network_access" in c for c in cmd),
                  f"no config: codex writer argv is not widened: {cmd[:8]}")
            check(resolve_default_timeout("codex", "dev", str(repo)) == 1800.0, "codex writer default timeout is 1800 s")
            check(resolve_default_timeout("claude-code", "dev", str(repo)) == 300.0, "claude-code default stays 300 s")
            check(resolve_default_timeout("antigravity", "sre", str(repo)) == 300.0, "antigravity default stays 300 s")
            check(resolve_default_timeout("codex", "rev", str(repo)) == 300.0, "codex read-only reviewer keeps 300 s")
            res, err, _ = prep()
            check(res["default_timeout"] == 1800.0 and res["codex_writable_dirs"] == []
                  and res["codex_network_access"] is False and res["codex_sandbox_source"] is None
                  and "ignoring" not in err, "dry-run reports the 1800 s default and an unwidened sandbox")

            # 2. SEC-1: a checkout team.toml cannot widen the sandbox; timeout still works.
            (team / "team.toml").write_text(
                '[orchestration]\ncodex_timeout_sec = 2400\n'
                f'codex_writable_dirs = ["{root}/cache/ok"]\ncodex_network_access = true\n', encoding="utf-8")
            check(resolve_default_timeout("codex", "dev", str(repo)) == 2400.0, "team.toml codex_timeout_sec applies")
            cmd = build_harness_command("codex", "dev", prompt, cwd=str(repo))
            check("--add-dir" not in cmd and not any("network_access" in c for c in cmd),
                  "repo team.toml codex_writable_dirs / codex_network_access never reach argv")
            res, err, _ = prep()
            check(res["exit_code"] == 0 and res["codex_writable_dirs"] == [] and res["codex_network_access"] is False
                  and res["codex_timeout_source"].endswith("team.toml") and res["default_timeout"] == 2400.0,
                  "repo-supplied widening is ignored (dispatch proceeds), timeout source reported")
            check("ignoring codex_writable_dirs, codex_network_access" in err and "codex.toml" in err,
                  f"stderr warns and points to the user-level file: {err!r}")

            # 3. User-level file keyed by repo realpath: applied, reported, validated per entry.
            cfg.write_text(
                f'[repos."{top}"]\ncodex_writable_dirs = ["{root}/cache/ok", "{root}/cache/ok/", "~/toolchain"]\n'
                f'codex_network_access = true\n'
                f'[repos."{root}/work/other"]\ncodex_writable_dirs = ["/nonexistent-other"]\n', encoding="utf-8")
            cmd = build_harness_command("codex", "dev", prompt, cwd=str(repo))
            i = cmd.index("--sandbox")
            check(cmd[i:i + 9] == ["--sandbox", "workspace-write", "--add-dir", f"{root}/cache/ok",
                                    "--add-dir", f"{home}/toolchain",
                                    "-c", "sandbox_workspace_write.network_access=true", "-m"],
                  f"user-level dirs (realpath, de-duplicated) and network reach codex exec: {cmd[:14]}")
            check(cmd[-1] == f"Act as the mARC engineer specialist. {prompt}", "prompt stays the last argv element")
            rcmd = build_harness_command("codex", "rev", prompt, codex_opt_in=True, cwd=str(repo))
            check("--add-dir" not in rcmd and not any("network_access" in c for c in rcmd),
                  "codex read-only reviewer argv never gets the writer widening")
            res, err, _ = prep()
            check(res["codex_writable_dirs"] == [f"{root}/cache/ok", f"{home}/toolchain"]
                  and res["codex_network_access"] is True and res["codex_sandbox_source"] == str(cfg)
                  and "widened by" in err and str(cfg) in err, "result JSON and stderr report widening and its source")
            check(_validate_spec({"prompt": prompt, "cwd": str(repo), "prepared": res}) is res,
                  "detached runner re-derives the same widened argv")
            # A linked worktree of the repo matches the main checkout's section.
            wt = root / "work" / "linked"
            _git_env_run("worktree", "add", "-q", "--detach", str(wt), cwd=repo)
            check("--add-dir" in build_harness_command("codex", "dev", prompt, cwd=str(wt)),
                  "a linked worktree uses its main checkout's section")
            # Another repo's section is not applied here.
            other = root / "work" / "unlisted"
            _init_repo(other)
            check("--add-dir" not in build_harness_command("codex", "dev", prompt, cwd=str(other)),
                  "a repo without a section gets no widening")

            # 4. SEC-2: every bad directory class fails closed (exit 2) with a specific message.
            (root / "cache" / "sshlink").symlink_to(home / ".ssh")
            bad_dirs = {
                "/": "filesystem root", str(home): "home directory", f"{home}/../": "ancestors",
                str(root): "ancestors", f"{root}/work/../home": "home directory",
                str(repo): "checkout", str(root / "work"): "checkout", str(repo / ".git"): "git directory",
                str(repo / ".git" / "hooks"): "git directory", "/etc": "system tree", "/usr": "system tree",
                "/proc": "system tree", str(home / ".ssh"): "credential", str(home / ".codex"): "credential",
                str(root / "cache" / "sshlink"): "credential", str(home / ".config"): "sandbox config",
                str(home / ".config" / "marc"): "sandbox config", f"{root}/cache/missing": "not an existing directory",
                "relative/dir": "absolute", "-rf": "absolute",
            }
            for d, why in bad_dirs.items():
                if d.startswith("/") and not os.path.exists(d) and why != "not an existing directory":
                    continue
                cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{d}"]\n', encoding="utf-8")
                res, err, _ = prep()
                check(res["exit_code"] == 2 and not res["command"] and why in res["error"],
                      f"bad writable dir {d!r} fails closed ({why}): {res.get('error')}")
            # Linked worktree: the main checkout is still denied; a symlinked config keeps its dir denied.
            cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{repo}"]\n', encoding="utf-8")
            res, _e, _ = _capture_stderr(prepare_dispatch, "dev", prompt, harness="codex", dry_run=True,
                                         which_fn=which, cwd=wt)
            check(res["exit_code"] == 2 and "checkout" in res["error"],
                  f"main checkout is denied from a linked worktree: {res.get('error')}")
            real_cfg = root / "realcfg" / "codex.toml"
            real_cfg.parent.mkdir()
            real_cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{home}/.config/marc"]\n', encoding="utf-8")
            cfg.unlink()
            cfg.symlink_to(real_cfg)
            res, _e, _ = prep()
            check(res["exit_code"] == 2 and "sandbox config" in res["error"],
                  f"the config symlink's own directory is denied: {res.get('error')}")
            cfg.unlink()
            for line in ('codex_writable_dirs = "/opt/x"', 'codex_writable_dirs = [3]',
                         'codex_network_access = "yes"'):
                cfg.write_text(f'[repos."{top}"]\n{line}\n', encoding="utf-8")
                res, err, _ = prep()
                check(res["exit_code"] == 2 and not res["command"], f"malformed user setting fails closed: {line}")
            # A bad entry never blocks another harness.
            res = prepare_dispatch("dev", prompt, harness="claude-code", dry_run=True, which_fn=which, cwd=repo)
            check(res["exit_code"] == 0, "bad codex sandbox entry does not block a claude-code dispatch")

            # SEC-9: symlinks planted where others could write are refused; own links stay fine.
            open_dir = root / "open"
            open_dir.mkdir()
            open_dir.chmod(0o777)
            (open_dir / "lnk").symlink_to(root / "cache" / "ok")
            (root / "cache" / "oklink").symlink_to(root / "cache" / "ok")
            for d, ok in ((f"{open_dir}/lnk", False), (f"{root}/cache/oklink", True)):
                cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{d}"]\n', encoding="utf-8")
                res, err, _ = prep()
                check((res["exit_code"] == 0) == ok and (ok or "world-writable directory" in res["error"]),
                      f"symlink dir {d!r} {'accepted' if ok else 'refused'}: {res.get('error')}")
            # SEC-2 residual: persistence dirs denied, toolchain caches allowed.
            for rel in (".local/bin", ".config/systemd/user", ".config/autostart", ".bashrc.d"):
                (home / rel).mkdir(parents=True, exist_ok=True)
                cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{home}/{rel}"]\n', encoding="utf-8")
                res, err, _ = prep()
                check(res["exit_code"] == 2 and "credential or agent" in res["error"], f"~/{rel} denied")
            for sysdir in ("/opt", "/mnt", "/media", "/srv"):
                if os.path.isdir(sysdir):
                    cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{sysdir}"]\n', encoding="utf-8")
                    res, err, _ = prep()
                    check(res["exit_code"] == 2 and "system tree" in res["error"], f"{sysdir} denied")
            (home / ".cargo" / "registry").mkdir(parents=True)
            cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{home}/.cargo"]\n', encoding="utf-8")
            check(prep()[0]["exit_code"] == 0, "~/.cargo (toolchain cache) stays allowed")
            # SEC-6: relative or empty [repos] keys are skipped with a warning.
            cfg.write_text(f'[repos.""]\ncodex_network_access = true\n[repos."work/repo"]\n'
                           f'codex_network_access = true\n[repos."{top}"]\ncodex_writable_dirs = []\n',
                           encoding="utf-8")
            res, err, _ = prep()
            check(res["exit_code"] == 0 and res["codex_network_access"] is False and err.count("must be an absolute path") >= 2,
                  f"relative/empty [repos] keys skipped with warning: {err!r}")
            # SEC-8: group/world-writable config refused; chmod go-w fixes it.
            cfg.write_text(f'[repos."{top}"]\ncodex_network_access = true\n', encoding="utf-8")
            cfg.chmod(0o664)
            res, err, _ = prep()
            check(res["exit_code"] == 2 and "chmod go-w" in res["error"], f"group-writable config refused: {res.get('error')}")
            cfg.chmod(0o600)
            check(prep()[0]["codex_network_access"] is True, "0600 config accepted")
            # SEC-7: from a linked worktree, a config inside the MAIN checkout is refused too.
            main_xdg = repo / "cfg2"
            (main_xdg / "marc").mkdir(parents=True)
            (main_xdg / "marc" / "codex.toml").write_text(cfg.read_text(), encoding="utf-8")
            with _IsoEnv(home, xdg=main_xdg):
                res, _e, _ = _capture_stderr(prepare_dispatch, "dev", prompt, harness="codex", dry_run=True,
                                             which_fn=which, cwd=wt)
                check(res["exit_code"] == 2 and "inside the checkout" in res["error"],
                      f"config inside the main checkout refused from a linked worktree: {res.get('error')}")

            # 5. The user-level file may not live inside the checkout (directly or via symlink).
            cfg.write_text(f'[repos."{top}"]\ncodex_writable_dirs = ["{root}/cache/ok"]\n', encoding="utf-8")
            inside = repo / "cfg"
            (inside / "marc").mkdir(parents=True)
            (inside / "marc" / "codex.toml").write_text(cfg.read_text(), encoding="utf-8")
            with _IsoEnv(home, xdg=inside):
                res, err, _ = prep()
                check(res["exit_code"] == 2 and "inside the checkout" in res["error"],
                      "user-level file inside the checkout is refused")
            sym = root / "xdg2"
            (sym / "marc").mkdir(parents=True)
            (sym / "marc" / "codex.toml").symlink_to(inside / "marc" / "codex.toml")
            with _IsoEnv(home, xdg=sym):
                res, err, _ = prep()
                check(res["exit_code"] == 2 and "inside the checkout" in res["error"],
                      "a symlink into the checkout is refused too")

            # 6. SEC-4: timeout cap; CLI --timeout stays uncapped.
            cfg.unlink()
            for val, ok in ((7200, True), (7200.5, False), (7201, False), (0, False), ("900", False),
                            ("nan", False), ("inf", False), ("-inf", False), ("true", False)):
                raw = f'"{val}"' if val == "900" else val
                (team / "team.toml").write_text(f"[orchestration]\ncodex_timeout_sec = {raw}\n", encoding="utf-8")
                res, err, _ = prep()
                check((res["exit_code"] == 0) == ok, f"codex_timeout_sec = {val!r} {'accepted' if ok else 'refused'}")
            (team / "team.toml").write_text("[orchestration]\ncodex_timeout_sec = 2400\n", encoding="utf-8")

            # 7. REV-2: no tomllib (Python < 3.11): explicit message, never a misleading error.
            saved = sys.modules.get("tomllib", "absent")
            sys.modules["tomllib"] = None  # type: ignore[assignment]
            try:
                res, err, _ = prep()
                check(res["exit_code"] == 2 and "requires Python 3.11+ (tomllib)" in res["error"],
                      f"codex_timeout_sec without tomllib: explicit message: {res.get('error')}")
                (team / "team.toml").write_text("[orchestration]\n", encoding="utf-8")
                check(prep()[0]["exit_code"] == 0, "no codex keys, no user file: works without tomllib")
                cfg.write_text(f'[repos."{top}"]\ncodex_network_access = true\n', encoding="utf-8")
                res, err, _ = prep()
                check(res["exit_code"] == 2 and "requires Python 3.11+ (tomllib)" in res["error"],
                      "user-level file without tomllib: explicit message")
                cfg.unlink()
            finally:
                if saved == "absent":
                    sys.modules.pop("tomllib", None)
                else:
                    sys.modules["tomllib"] = saved

            # 8. REV-4: --team-toml elsewhere: its codex_* keys are ignored, with a warning.
            elsewhere = root / "elsewhere.toml"
            elsewhere.write_text("[orchestration]\ncodex_timeout_sec = 5000\ncodex_network_access = true\n",
                                 encoding="utf-8")
            (team / "team.toml").write_text("[orchestration]\ncodex_timeout_sec = 2400\n", encoding="utf-8")
            res, err, _ = prep(team_toml=str(elsewhere))
            check(res["default_timeout"] == 2400.0 and res["codex_network_access"] is False
                  and res["codex_timeout_source"] == str(team / "team.toml")
                  and "--team-toml" in err and "ignored" in err,
                  f"--team-toml codex_* keys ignored, source is the checkout's team.toml: {err!r}")
            (team / "team.toml").write_text("[orchestration]\ncodex_timeout_sec = 2400\ncodex_network_access = true\n",
                                            encoding="utf-8")
            res, err, _ = prep(team_toml=str(team / "team.toml"))
            check("--team-toml" not in err and "ignoring codex_network_access" in err,
                  f"--team-toml naming the discovered file: only the repo-key warning: {err!r}")
            (team / "team.toml").write_text("[orchestration]\ncodex_timeout_sec = 2400\n", encoding="utf-8")

        # 9. dispatch(): the resolved default reaches the runner; --timeout beats it, uncapped.
        with _IsoEnv(home):
            seen: List[float] = []

            def ok_runner(cmd, **kw):
                seen.append(kw["timeout"])
                return subprocess.CompletedProcess(cmd, 0, "", "")
            old = os.getcwd()
            try:
                os.chdir(repo)
                dispatch("dev", prompt, harness="codex", which_fn=which, runner_fn=ok_runner)
                dispatch("dev", prompt, harness="codex", timeout=99999.0, which_fn=which, runner_fn=ok_runner)
                dispatch("dev", prompt, harness="claude-code", which_fn=which, runner_fn=ok_runner,
                         env={"CLAUDE_PLUGIN_ROOT": "/plugin"})
            finally:
                os.chdir(old)
            check(seen == [2400.0, 99999.0, 300.0],
                  f"codex writer uses team.toml timeout, --timeout wins uncapped, others 300 s: {seen}")

    # The literal traversal from the review, against the real HOME (only meaningful under /home).
    if os.path.realpath(os.path.expanduser("~")).startswith("/home/"):
        try:
            _check_writable_dir("/tmp/../home", "w", "/tmp/x", None, Path("/tmp/x/codex.toml"))
            check(False, "/tmp/../home is refused")
        except ModelPinError:
            check(True, "/tmp/../home is refused")

    # 10. `start` and the detached `_run`: default timeout resolution end to end.
    started: List[int] = []
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(os.path.realpath(tmp))
        _init_repo(t / "repo")
        (t / "home").mkdir()
        bin_dir = t / "bin"
        _make_stub_bin(bin_dir, ["codex"])
        env = _job_env(bin_dir)
        env.update(HOME=str(t / "home"), XDG_CONFIG_HOME=str(t / "home" / ".config"))
        try:
            def run_start(*extra: str) -> Dict[str, Any]:
                r = _cli(["start", "--role", "dev", "--harness", "codex", *extra, "--prompt", "exit=0"],
                         t / "repo", env)
                out = _lines(r.stdout)
                check(r.returncode == 0 and len(out) == 1, f"start codex writer exits 0 ({r.returncode}, {r.stderr!r})")
                started.append(out[0]["pid"])
                w = _cli(["wait", out[0]["job_id"], "--timeout", "60"], t / "repo", env)
                res = json.loads(Path(out[0]["result_path"]).read_text(encoding="utf-8"))
                check(w.returncode == 0, f"codex stub job finishes ({w.returncode}, {w.stdout!r})")
                return res
            check(run_start()["timeout_sec"] == 1800.0, "start/_run: codex writer defaults to 1800 s")
            (t / "repo" / ".agents").mkdir()
            (t / "repo" / ".agents" / "team.toml").write_text("[orchestration]\ncodex_timeout_sec = 2400\n",
                                                               encoding="utf-8")
            check(run_start()["timeout_sec"] == 2400.0, "start/_run: team.toml codex_timeout_sec applies")
            check(run_start("--timeout", "42")["timeout_sec"] == 42.0, "start/_run: --timeout beats the config")
        finally:
            for pid in started:
                _kill_group(pid)

    # 11. Timeout: killpg in the sync path (SEC-4), and attributable partial work (REV-1).
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(os.path.realpath(tmp))
        (d / "home").mkdir()
        bin_dir = d / "bin"
        _make_stub_bin(bin_dir, ["codex"])
        pidfile = d / "child.pid"
        # Stub forks a grandchild that outlives a plain kill of the direct child.
        (bin_dir / "codex").write_text(
            f"#!{sys.executable}\nimport os,sys,time\n"
            f"pid=os.fork()\n"
            f"if pid==0:\n    open({str(pidfile)!r},'w').write(str(os.getpid())); time.sleep(60); sys.exit(0)\n"
            f"time.sleep(60)\n", encoding="utf-8")
        (bin_dir / "codex").chmod(0o755)
        from dispatch_agent import _run_sync_in_group
        import time as _t
        try:
            _run_sync_in_group([str(bin_dir / "codex")], timeout=1.5)
            check(False, "sync group runner raises on timeout")
        except subprocess.TimeoutExpired:
            check(True, "sync group runner raises TimeoutExpired")
        gp = int(pidfile.read_text()) if pidfile.exists() else 0
        check(gp > 0 and _pid_gone(gp, 5), "sync Codex timeout kills the whole process group (grandchild gone)")
        if gp:
            _kill_group(gp)

        # SIGTERM to the dispatching process must not orphan Codex.
        pidfile.unlink()
        driver = (f"import sys; sys.path.insert(0, {HERE!r}); import dispatch_agent as d; "
                  f"d._run_sync_in_group([{str(bin_dir / 'codex')!r}], timeout=60)")
        par = subprocess.Popen([sys.executable, "-c", driver])
        for _ in range(100):
            if pidfile.exists() and pidfile.read_text():
                break
            _t.sleep(0.1)
        gp = int(pidfile.read_text()) if pidfile.exists() and pidfile.read_text() else 0
        par.terminate()
        par.wait(timeout=10)
        check(gp > 0 and _pid_gone(gp, 5), "SIGTERM to the sync dispatcher kills Codex's process group")
        if gp:
            _kill_group(gp)

    with tempfile.TemporaryDirectory() as tmp:
        d = Path(os.path.realpath(tmp))
        (d / "home").mkdir()
        repo = d / "repo"
        _init_repo(repo)
        managed = d / "home" / ".codex" / "worktrees"  # CODEX_HOME comes from _IsoEnv, not this machine
        wt = managed / "ab12" / "repo"
        wt2 = managed / "cd34" / "repo"
        foreign = d / "other-operator-wt"
        which = lambda b: f"/usr/bin/{b}"

        def run(runner):
            old = os.getcwd()
            try:
                os.chdir(repo)
                with _IsoEnv(d / "home"):
                    return dispatch("dev", prompt, harness="codex", timeout=5.0, which_fn=which, runner_fn=runner)
            finally:
                os.chdir(old)

        def add(path: Path) -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            _git_env_run("worktree", "add", "-q", "--detach", str(path), cwd=repo)

        def one(cmd, **kw):
            add(wt)
            add(foreign)  # a concurrent operator's non-Codex worktree must not be reported
            (wt / "a.txt").write_text("one\ntwo\n", encoding="utf-8")
            (wt / "half.txt").write_text("partial", encoding="utf-8")
            raise subprocess.TimeoutExpired(cmd, kw["timeout"], output=b"partial out")
        res = run(one)
        pw = res.get("partial_work") or {}
        wts = pw.get("worktrees") or []
        check(res["exit_code"] == 124 and res.get("timed_out") is True, "codex timeout still reports 124/timed_out")
        check(len(wts) == 1 and os.path.realpath(wts[0]["path"]) == str(wt),
              f"partial_work names the one new Codex-managed worktree: {wts}")
        if wts:
            check(" M a.txt" in wts[0]["git_status"] and "?? half.txt" in wts[0]["git_status"]
                  and wts[0]["has_changes"] is True, f"git status lists modified and untracked: {wts[0]['git_status']!r}")
            check("a.txt" in wts[0]["diff_stat"], f"diff stat reported: {wts[0]['diff_stat']!r}")
        check(pw.get("candidates") == [{"path": str(foreign), "attributed": False}],
              f"a worktree outside the managed root is a path-only candidate, not dropped: {pw.get('candidates')}")
        check(wts and wts[0]["attributed"] == "likely", "a lone managed worktree is 'likely', never certain")
        check(wt.is_dir() and (wt / "half.txt").exists(), "the timed-out worktree is preserved, not cleaned up")

        # Two new Codex-managed worktrees: nothing attributed, no remove suggestion.
        def two(cmd, **kw):
            add(wt2)
            add(managed / "ef56" / "repo")
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        res = run(two)
        pw = res["partial_work"]
        check(not pw["worktrees"] and len(pw["candidates"]) == 2, f"two new Codex worktrees: candidates only: {pw}")
        check(all(c["attributed"] is False for c in pw["candidates"]) and "git worktree remove" not in json.dumps(pw),
              f"ambiguous worktrees are unattributed candidates, no removal advice: {pw}")

        # Only a foreign worktree appears: none found.
        foreign2 = d / "foreign2"

        def only_foreign(cmd, **kw):
            add(foreign2)
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        res = run(only_foreign)
        check(res["partial_work"]["worktrees"] == []
              and res["partial_work"]["candidates"] == [{"path": str(foreign2), "attributed": False}],
              f"only a non-managed worktree appeared: a candidate, nothing attributed: {res['partial_work']}")

        # REV-8: the managed root is realpath(CODEX_HOME)/worktrees; a symlinked CODEX_HOME works,
        # and a look-alike `.agents/codex/worktrees` path elsewhere is NOT treated as managed.
        real_ch = d / "realcodex"
        (real_ch / "worktrees").mkdir(parents=True)
        link_ch = d / "linkcodex"
        link_ch.symlink_to(real_ch)
        lookalike = d / "x" / ".agents" / "codex" / "worktrees" / "zz" / "repo"
        lookalike.parent.mkdir(parents=True)

        def via_symlink(cmd, **kw):
            _git_env_run("worktree", "add", "-q", "--detach", str(real_ch / "worktrees" / "q1"), cwd=repo)
            _git_env_run("worktree", "add", "-q", "--detach", str(lookalike), cwd=repo)
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        old = os.getcwd()
        try:
            os.chdir(repo)
            with _IsoEnv(d / "home", codex_home=link_ch):
                res = dispatch("dev", prompt, harness="codex", timeout=5.0, which_fn=which, runner_fn=via_symlink)
        finally:
            os.chdir(old)
        pw = res["partial_work"]
        check(len(pw["worktrees"]) == 1 and pw["worktrees"][0]["path"].endswith("worktrees/q1")
              and pw["candidates"] == [{"path": str(lookalike), "attributed": False}],
              f"symlinked CODEX_HOME matched by realpath prefix; look-alike path only a candidate: {pw}")

        # REV-6a: a failing git status is has_changes null with an explicit error, never false.
        import dispatch_agent as _da
        real_git = _da._git_safe
        _da._git_safe = lambda args, cwd=None, timeout=20.0: (None if args[0] == "status" else real_git(args, cwd, timeout))
        try:
            def one_more(cmd, **kw):
                _git_env_run("worktree", "add", "-q", "--detach", str(managed / "st" / "repo"), cwd=repo)
                raise subprocess.TimeoutExpired(cmd, kw["timeout"])
            res = run(one_more)
        finally:
            _da._git_safe = real_git
        e0 = (res["partial_work"]["worktrees"] or [{}])[0]
        check(e0.get("has_changes") is None and "failed" in e0.get("error", ""),
              f"git failure: has_changes null plus explicit error: {e0}")

        # REV-6b: no git checkout (snapshot fails): a timed-out Codex writer still gets partial_work.
        nogit = d / "nogit"
        nogit.mkdir()

        def plain(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        old = os.getcwd()
        try:
            os.chdir(nogit)
            with _IsoEnv(d / "home"):
                res = dispatch("dev", prompt, harness="codex", timeout=5.0, which_fn=which, runner_fn=plain)
        finally:
            os.chdir(old)
        check(res.get("timed_out") is True and "could not snapshot" in (res.get("partial_work") or {}).get("note", ""),
              f"snapshot failure still yields an explained partial_work: {res.get('partial_work')}")

        # REV-7: a setsid descendant holding the pipe must not stall the timeout return.
        sd = d / "setsid-bin"
        _make_stub_bin(sd, ["codex"])
        (sd / "codex").write_text(
            f"#!{sys.executable}\nimport os,time\n"
            f"if os.fork()==0:\n    os.setsid(); time.sleep(8); os._exit(0)\n"
            f"time.sleep(60)\n", encoding="utf-8")
        (sd / "codex").chmod(0o755)
        t0 = _t.time()
        try:
            _run_sync_in_group([str(sd / "codex")], timeout=1.0)
            check(False, "setsid case raises TimeoutExpired")
        except subprocess.TimeoutExpired:
            took = _t.time() - t0
            check(took < 5.0, f"timeout returns promptly despite a setsid pipe-holder ({took:.1f}s)")

        def plain_timeout(cmd, **kw):
            raise subprocess.TimeoutExpired(cmd, kw["timeout"])
        old = os.getcwd()
        try:
            os.chdir(repo)
            res2 = dispatch("dev", prompt, harness="claude-code", timeout=5.0, which_fn=which,
                            runner_fn=plain_timeout, env={"CLAUDE_PLUGIN_ROOT": "/plugin"})
        finally:
            os.chdir(old)
        check(res2.get("timed_out") is True and "partial_work" not in res2, "non-codex timeout has no partial_work")


def _fake_bin(d: Path, name: str) -> str:
    d.mkdir(parents=True, exist_ok=True)
    f = d / name
    f.write_text("#!/bin/sh\n")
    f.chmod(0o755)
    return str(f)


def test_harness_detect_409() -> None:
    """Probe, operator opt-in, read-only fail-closed, team.toml cannot widen writers (#409)."""
    with tempfile.TemporaryDirectory() as td:
        t = Path(td).resolve()
        home = t / "home"
        (home / ".config" / "marc").mkdir(parents=True)
        repo = t / "repo"
        repo.mkdir()
        _git_env_run("init", "-q", cwd=repo)
        (repo / ".agents").mkdir()
        tt = repo / ".agents" / "team.toml"
        tt.write_text('[orchestration]\n[orchestration.routes]\ndev = "codex"\nsre = "antigravity"\n'
                      'sec = "codex"\n', encoding="utf-8")
        binroot = t / "bin"
        paths = {b: _fake_bin(binroot, b) for b in ("claude", "codex", "agy")}
        repo_bin = _fake_bin(repo / "bin", "copilot")
        which = lambda b: {**paths, "copilot": repo_bin}.get(b)
        calls: List[Any] = []

        def run_fn(cmd: Any, **kw: Any) -> Any:
            calls.append((cmd, kw))
            return MagicMock(stdout="codex-cli 9.9\nextra", returncode=0)

        cc_env = {"CLAUDE_PROJECT_DIR": str(repo), "SECRET_TOKEN": "x"}
        with _IsoEnv(home):
            rep = probe_harnesses(which_fn=which, run_fn=run_fn, env=cc_env, cwd=str(repo))
            by = {e["harness"]: e for e in rep["harnesses"]}
            check(rep["native"] == "claude-code" and by["claude-code"]["native"], "probe: native detected")
            check(by["codex"]["status"] == "present" and by["codex"]["writer_roles"], "probe: codex present, writer-eligible")
            check(by["antigravity"]["status"] == "present" and not by["antigravity"]["writer_roles"],
                  "probe: agy detected but never writer-eligible")
            check(by["copilot"]["status"] == "untrusted-path", "probe: binary inside the checkout is untrusted-path")
            check(by["claude-code"]["read_only_roles"] and not by["codex"]["read_only_roles"],
                  "probe: read-only roles only claude-code")
            check(calls == [] and rep["ask_needed"] and rep["writers"] is None,
                  "probe: executes nothing by default; asks once when nothing recorded")
            rep = probe_harnesses(which_fn=which, run_fn=run_fn, env=cc_env, cwd=str(repo), probe=True)
            ver = [e for e in rep["harnesses"] if e["harness"] == "codex"][0]["version"]
            check(ver == "codex-cli 9.9" and all(k["stdin"] == subprocess.DEVNULL and k["timeout"] == 3.0
                                                 and "SECRET_TOKEN" not in k["env"] and k["cwd"] != str(repo)
                                                 for _, k in calls) and len(calls) == 3,
                  f"probe --probe: --version only, bounded, scrubbed env, temp cwd, never the untrusted binary ({ver})")
            solo = probe_harnesses(which_fn=lambda b: paths["claude"] if b == "claude" else None,
                                   env=cc_env, cwd=str(repo))
            check(harnesses_line(solo) == "", "line: silent when only the native harness is present")
            check(harnesses_line(rep).startswith("harnesses: claude-code (native) · antigravity (on PATH)")
                  and "ask once" in harnesses_line(rep), "line: one line with the ask hint")

            # Without opt-in: team.toml writer routes fall back native, with a warning naming the entry.
            err = io.StringIO()
            old = sys.stderr
            sys.stderr = err
            try:
                r = resolve_route_detailed("dev", "auto", tt, env=cc_env, which_fn=which, cwd=str(repo))
                r2 = resolve_route_detailed("sre", "auto", tt, env=cc_env, which_fn=which, cwd=str(repo))
            finally:
                sys.stderr = old
            check(r["harness"] == "claude-code" and r2["harness"] == "claude-code",
                  "team.toml alone cannot widen writer routing (dev->codex, sre->agy run native)")
            check('writers = "codex"' in err.getvalue() and f'[repos."{repo}"]' in err.getvalue()
                  and "harnesses.toml" in err.getvalue(), "broken legacy route warns with the exact opt-in entry")

            # Read-only roles: team.toml codex route without #351 shape still fails closed; unchanged.
            for role in ("sec", "rev", "research", "arch", "bulk-reader"):
                rr = resolve_route_detailed(role, "auto", None, env=cc_env, which_fn=which, cwd=str(repo))
                check(rr["harness"] == "claude-code", f"read-only {role} stays claude-code with no opt-in")

            cfg = home / ".config" / "marc" / "harnesses.toml"
            cfg.write_text(record_block(str(repo), "codex"), encoding="utf-8")
            check(load_writer_opt_in(str(repo))["writers"] == "codex", "opt-in loads from the operator file")
            for role in ("dev", "sre", "design"):
                rr = resolve_route_detailed(role, "auto", None, env=cc_env, which_fn=which, cwd=str(repo))
                check(rr["harness"] == "codex", f"opt-in enables codex for writer {role} (no team.toml needed)")
            r = resolve_route_detailed("dev", "auto", tt, env=cc_env, which_fn=which, cwd=str(repo))
            check(r["harness"] == "codex", "team.toml codex route is honoured once the opt-in confirms it")
            sys.stderr = io.StringIO()
            try:
                r2 = resolve_route_detailed("sre", "auto", tt, env=cc_env, which_fn=which, cwd=str(repo))
            finally:
                sys.stderr = old
            check(r2["harness"] == "claude-code", "opt-in for codex does not confirm a team.toml route to agy")
            sys.stderr = io.StringIO()
            try:
                for role in ("rev", "research", "arch", "bulk-reader"):
                    rr = resolve_route_detailed(role, "auto", tt, env=cc_env, which_fn=which, cwd=str(repo))
                    check(rr["harness"] == "claude-code" and not rr["codex_readonly_opt_in"],
                          f"read-only {role} unaffected by writers = codex")
            finally:
                sys.stderr = old
            nat = tt.parent / "native.toml"
            nat.write_text('[orchestration]\nmode = "native"\n', encoding="utf-8")
            r = resolve_route_detailed("dev", "auto", nat, env=cc_env, which_fn=which, cwd=str(repo))
            check(r["harness"] == "claude-code", "team.toml mode=native narrows even with the opt-in")

            # The operator file is validated like codex.toml: inside the checkout, or group-writable, is refused.
            cfg.chmod(0o666)
            sys.stderr = io.StringIO()
            try:
                bad = load_writer_opt_in(str(repo))
            finally:
                sys.stderr = old
            check(bad["writers"] is None, "group/world-writable opt-in file is ignored (fails closed)")
            cfg.chmod(0o644)
            cfg.write_text('[repos."%s"]\nwriters = "agy"\n' % repo, encoding="utf-8")
            sys.stderr = io.StringIO()
            try:
                bad = load_writer_opt_in(str(repo))
            finally:
                sys.stderr = old
            check(bad["writers"] is None, "writers = <not codex|native> is rejected")
        with _IsoEnv(home, xdg=repo / "cfg"):
            (repo / "cfg" / "marc").mkdir(parents=True)
            (repo / "cfg" / "marc" / "harnesses.toml").write_text(record_block(str(repo), "codex"), encoding="utf-8")
            sys.stderr = io.StringIO()
            try:
                inside = load_writer_opt_in(str(repo))
            finally:
                sys.stderr = old
            check(inside["writers"] is None, "an opt-in file inside the checkout cannot enable itself")
        # Direct resolver: a read-only role with writer_opt_in set still fails closed.
        d = resolve_target_harness_detailed("claude-code", "rev", "auto", None, None, lambda b: True,
                                            writer_opt_in="codex")
        check(d["harness"] == "claude-code", "resolver: writer_opt_in never reaches read-only roles")


def test_harness_detect_409_fixes() -> None:
    """Round-1 review fixes: claude-code routes, binary trust, main-checkout key, escaping (#409)."""
    from dispatch_agent import _binary_hazard, _toml_basic_string  # noqa: F401
    import dispatch_agent as da
    old = sys.stderr
    with tempfile.TemporaryDirectory() as td:
        t = Path(td).resolve()
        home = t / "home"
        (home / ".config" / "marc").mkdir(parents=True)
        repo = t / "repo"
        repo.mkdir()
        _git_env_run("init", "-q", cwd=repo)
        _git_env_run("commit", "--allow-empty", "-qm", "x", cwd=repo)
        (repo / ".agents").mkdir()
        tt = repo / ".agents" / "team.toml"
        tt.write_text('[orchestration]\n[orchestration.routes]\ndev = "claude-code"\n', encoding="utf-8")
        binroot = t / "bin"
        paths = {b: _fake_bin(binroot, b) for b in ("claude", "codex", "agy", "copilot")}
        which = lambda b: paths.get(b)
        with _IsoEnv(home):
            # REV-1: an explicit route to claude-code is honoured on every host (it only narrows).
            for name, env in (("codex", {"PLUGIN_ROOT": "/p"}), ("antigravity", {"ANTIGRAVITY_CONVERSATION_ID": "c"}),
                              ("copilot", {"COPILOT_PLUGIN_DATA": "/d"})):
                r = resolve_route_detailed("dev", "auto", tt, env=env, which_fn=which, cwd=str(repo))
                check(r["harness"] == "claude-code" and not r["fallback"],
                      f"team.toml dev = claude-code is honoured on a {name} host (got {r['harness']})")

            # REV-4: the writer-widening override reports fallback and its reason.
            tt2 = repo / ".agents" / "w.toml"
            tt2.write_text('[orchestration]\n[orchestration.routes]\ndev = "copilot"\n', encoding="utf-8")
            sys.stderr = io.StringIO()
            try:
                r = resolve_route_detailed("dev", "auto", tt2, env={"CLAUDE_PLUGIN_ROOT": "/p"}, which_fn=which, cwd=str(repo))
            finally:
                sys.stderr = old
            check(r["harness"] == "claude-code" and r["fallback"] and "opt-in" in (r["fallback_reason"] or "")
                  and not r["policy_reroute"], "override sets fallback + reason, not the #323 policy flag")

            # SEC-1: an opted-in codex resolved to an untrusted binary falls back native with a warning.
            (home / ".config" / "marc" / "harnesses.toml").write_text(record_block(str(repo), "codex"), encoding="utf-8")
            r = resolve_route_detailed("dev", "auto", None, env={"CLAUDE_PLUGIN_ROOT": "/p"}, which_fn=which, cwd=str(repo))
            check(r["harness"] == "codex", "trusted codex binary: opt-in routes to codex")
            bad_codex = _fake_bin(repo / "bin", "codex")
            sys.stderr = io.StringIO()
            try:
                r = resolve_route_detailed("dev", "auto", None, env={"CLAUDE_PLUGIN_ROOT": "/p"},
                                           which_fn=lambda b: bad_codex if b == "codex" else paths.get(b), cwd=str(repo))
                warn = sys.stderr.getvalue()
            finally:
                sys.stderr = old
            check(r["harness"] == "claude-code" and r["fallback"] and "untrusted" in (r["fallback_reason"] or "")
                  and "untrusted" in warn, "opt-in codex inside the checkout is untrusted: native + warning")

            # SEC-2: group-writable dir and symlinked paths (another-user ownership needs root to stage).
            gw = t / "gw"
            gw.mkdir()
            gbin = _fake_bin(gw, "codex")
            gw.chmod(0o755)
            check(_binary_hazard(gbin, str(repo), None) is None, "a private bin dir is trusted")
            wr = t / "wr"
            wr.mkdir()
            wr.chmod(0o777)
            lnk = wr / "lnk"
            os.symlink(str(binroot), str(lnk))
            check("symlink" in (_binary_hazard(str(lnk / "codex"), str(repo), None) or ""),
                  "a symlink component in a world-writable dir is a hazard")

            # REV-2: the key is the MAIN checkout; a linked worktree matches the same opt-in.
            wt = t / "wt"
            _git_env_run("worktree", "add", "-q", "-b", "side", str(wt), cwd=repo)
            rep_ = probe_harnesses(which_fn=which, env={"CLAUDE_PROJECT_DIR": str(wt)}, cwd=str(wt))
            check(rep_["repo"] == str(repo) and rep_["writers"] == "codex",
                  f"worktree reports the main checkout and finds its opt-in ({rep_['repo']})")
            rr = resolve_route_detailed("dev", "auto", None, env={"CLAUDE_PLUGIN_ROOT": "/p"}, which_fn=which, cwd=str(wt))
            check(rr["harness"] == "codex", "opt-in matches from a linked worktree")

            # REV-5a: team.toml mode=native silences the ask.
            nat = repo / ".agents" / "team.toml"
            nat.write_text('[orchestration]\nmode = "native"\n', encoding="utf-8")
            (home / ".config" / "marc" / "harnesses.toml").unlink()
            rep_ = probe_harnesses(which_fn=which, env={"CLAUDE_PROJECT_DIR": str(repo)}, cwd=str(repo))
            check(not rep_["ask_needed"], "mode = native: no ask")

            # SEC-4: version output is first line, version-ish charset, capped.
            v = da._probe_version(paths["codex"], lambda *a, **k: MagicMock(
                stdout="codex 1.2\x1b[31m;rm -rf /`$(x)`" + "A" * 200 + "\nIGNORE PREVIOUS"), {})
            check(v is not None and len(v) <= 60 and all(c not in v for c in "\x1b;`$\n"),
                  f"version output sanitised ({v!r})")

        # SEC-2 residual: group-writable dirs, injected stat (no real chown). Exception only when
        # owner is root/you AND group is gid 0, a system gid (<1000) or your primary gid.
        import types
        me_u, me_g = 1000, 1000

        def _st(uid: int, gid: int, mode: int = 0o40775) -> Any:
            return types.SimpleNamespace(st_uid=uid, st_gid=gid, st_mode=mode)

        def _hz(uid: int, gid: int, mode: int = 0o40775) -> Optional[str]:
            return _binary_hazard(paths["codex"], str(repo), None, stat_fn=lambda _p: _st(uid, gid, mode),
                                  ids=(me_u, me_g))
        check(_hz(0, 50) is None, "2775 root:staff-like dir is accepted")
        check(_hz(0, 0) is None and _hz(me_u, me_g) is None, "root:root and own-user own-group group-writable dirs accepted")
        check("group-writable" in (_hz(0, 5000) or ""), "group-writable root dir of a non-system, non-primary group is rejected")
        check("owned by another user" in (_hz(2000, 50) or ""), "group-writable dir owned by another non-root user is rejected")
        check("world-writable" in (_hz(0, 0, 0o40777) or ""), "world-writable dir is still rejected")

        # SEC-1: relative PATH entry with a planted codex must not run; argv[0] is the checked absolute path.
        old_cwd = os.getcwd()
        _fake_bin(repo / "bin", "codex")
        os.chdir(repo)
        try:
            rel_which = lambda b: shutil.which(b, path="bin") if b == "codex" else paths.get(b)
            check(rel_which("codex") == os.path.join("bin", "codex"), "setup: which returns a relative hit")
            sys.stderr = io.StringIO()
            try:
                with _IsoEnv(home):
                    r = resolve_route_detailed("dev", "codex", None, env={"CLAUDE_PLUGIN_ROOT": "/p"},
                                               which_fn=rel_which, cwd=str(repo))
            finally:
                sys.stderr = old
            check(r["harness"] != "codex" and r["fallback"] and "untrusted" in (r["fallback_reason"] or ""),
                  f"relative PATH entry with a planted codex falls back ({r['harness']})")
        finally:
            os.chdir(old_cwd)
        with _IsoEnv(home):
            prep_ = prepare_dispatch("dev", "p", harness="codex", dry_run=True, which_fn=which, cwd=repo)
            ro_ = prepare_dispatch("rev", "p", harness="codex", dry_run=True, which_fn=which, cwd=repo)
        check(prep_.get("command", [""])[0] == os.path.realpath(paths["codex"]),
              f"codex writer argv[0] is the checked absolute path ({prep_.get('command', [''])[:1]})")
        rel_cmd = da.build_harness_command("codex", "dev", "p", cwd=str(repo), codex_bin="/abs/codex")
        check(rel_cmd[0] == "/abs/codex", "writer builder honours codex_bin")
        ro_cmd = da.build_harness_command("codex", "rev", "p", codex_opt_in=True, cwd=str(repo), codex_bin="/abs/codex")
        check(ro_cmd[0] == "/abs/codex", "read-only builder honours codex_bin")

        # REV-7: opt-in names codex but codex is untrusted -> hook line says native + reason, not "writers: codex".
        rep_u = {"native": "claude-code", "writers": "codex", "ask_needed": False, "opt_in_source": "x", "repo": "r",
                 "harnesses": [
                     {"harness": "claude-code", "native": True, "status": "present"},
                     {"harness": "codex", "native": False, "status": "untrusted-path",
                      "reason": "resolves inside the checkout"}]}
        line = da.harnesses_line(rep_u)
        check("writers: codex" not in line and "writers: native" in line and "untrusted" in line
              and "resolves inside the checkout" in line, f"untrusted opted-in codex hook line ({line})")

        # SEC-3: the recorded key round-trips through the TOML loader even with quotes/backslashes/newlines.
        try:
            import tomllib
            for odd in ('/a/"b"/c\\d', "/a/b\nc", "/pl\u00e1in/\u00fcber"):
                got = tomllib.loads(record_block(odd, "codex"))
                check(list(got["repos"]) == [odd] and got["repos"][odd]["writers"] == "codex",
                      f"record_block round-trips {odd!r}")
        except ImportError:
            pass


def main() -> int:
    with tempfile.TemporaryDirectory() as _iso, _IsoEnv(Path(_iso)):
        return _run_all()


def _run_all() -> int:
    test_command_generation()
    test_default_hybrid_matrix()
    test_route_resolution_from_toml()
    test_native_harness_detection()
    test_cli_availability_and_fallback()
    test_dry_run_and_execution()
    test_timeout_handling()
    test_json_and_cli_interface()
    test_read_only_roles_fail_closed()
    test_role_normalization_fail_closed()
    test_codex_readonly_opt_in()
    test_model_matrix_385()
    test_start_wait_jobs()
    test_arch_role_388()
    test_codex_writer_timeout_sandbox_partial_401()
    test_harness_detect_409()
    test_harness_detect_409_fixes()

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print("\ndispatch_agent self-test: OK (all test cases passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
