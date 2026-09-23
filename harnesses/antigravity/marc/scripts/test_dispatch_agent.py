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
  * Read-only roles never route to antigravity; fail closed when no enforcing harness (#323)
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
    NON_ENFORCING_HARNESSES,
    READ_ONLY_ROLES,
    ROLE_TO_AGENT,
    ReadOnlyRoutingError,
    UnknownRoleError,
    build_harness_command,
    detect_native_harness,
    dispatch,
    main as dispatch_main,
    normalize_role,
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
            "rev": "claude-code",
            "review": "claude-code",
            "research": "claude-code",
            "sre": "claude-code",
            "design": "claude-code",
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


def main() -> int:
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

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print("\ndispatch_agent self-test: OK (all test cases passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
