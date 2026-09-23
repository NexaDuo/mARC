#!/usr/bin/env python3
"""Cross-harness subagent delegation and poly-model routing (origin: #239, #241).

Stdlib only (no third-party dependencies). Can be invoked directly or imported:
    python3 dispatch_agent.py --role dev --prompt "Implement issue #123" --dry-run
    python3 dispatch_agent.py --role research --prompt "Investigate auth providers" --json

CLI Options:
    --role <role>         Specialist role (dev, sre, design, sec, rev, research, engineer, security, review).
    --prompt <prompt>     Task prompt with context, acceptance criteria, and constraints.
    --harness <harness>   Target harness (auto, native, claude-code, antigravity, copilot). Default: auto.
    --timeout <seconds>   Subprocess execution timeout in seconds. Default: 300.
    --json                Output result as JSON payload.
    --dry-run             Resolve command and print without executing.
    --team-toml <path>    Optional path to team.toml (default: discovered from repo root).

Routing resolution:
    - Priority 1: Explicit --harness argument (if not 'auto').
    - Priority 2: User explicit route in team.toml [orchestration.routes].
    - Priority 3: Embedded default hybrid matrix (DEFAULT_HYBRID_MATRIX) when auto or unrouted.
    - If target CLI binary is not found on PATH: logs warning to stderr and gracefully
      falls back to host/native harness.
    - Read-only roles (READ_ONLY_ROLES: rev/research/sec/bulk-reader and aliases) never
      run on antigravity, whatever the source of the route: agy headless does not load
      `--agent` definitions (#323). They are re-routed to claude-code (or a non-agy host)
      with a stderr diagnostic; if no such harness is available, dispatch fails with
      exit code 2 instead of running them unrestricted.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

ROLE_TO_AGENT: Dict[str, str] = {
    "dev": "engineer",
    "engineer": "engineer",
    "sre": "sre",
    "design": "design",
    "sec": "security",
    "security": "security",
    "rev": "review",
    "review": "review",
    "research": "research",
    # Minimal-tool worker (issue #320, #322/#323 review): read-only-by-design
    # specialist the read-guard hook delegates untrusted-file summarization
    # to. Kept distinct from "research" (which carries Bash/WebFetch/
    # WebSearch) precisely because it must have no execution surface.
    "bulk-reader": "bulk-reader",
}

CANONICAL_ROLES: Dict[str, str] = {
    "dev": "dev",
    "engineer": "dev",
    "sre": "sre",
    "design": "design",
    "sec": "sec",
    "security": "sec",
    "rev": "rev",
    "review": "rev",
    "research": "research",
    "bulk-reader": "bulk-reader",
}

HARNESS_BINARIES: Dict[str, str] = {
    "claude-code": "claude",
    "antigravity": "agy",
    "copilot": "copilot",
}

# Read-only roles (canonical names, see CANONICAL_ROLES): their agent definitions
# declare no Write/Edit tool, and the review/security gate relies on that. Issue
# #323: in headless `-p` mode `agy --agent <name>` does not load the named agent
# (it silently falls back to the default agent with every tool), so these roles
# must never be dispatched to antigravity. Aliases resolve through CANONICAL_ROLES.
READ_ONLY_ROLES = frozenset({"rev", "research", "sec", "bulk-reader"})

# Harnesses whose headless dispatch path does NOT apply mARC agent definitions
# (persona, model pin, `tools:` restriction). Issue #323, measured on agy 1.2.9.
NON_ENFORCING_HARNESSES = frozenset({"antigravity"})

# Preferred harness for read-only roles: claude-code resolves `--agent` and
# enforces `tools:` even under --dangerously-skip-permissions (measured, #320/#323).
READ_ONLY_PREFERRED_HARNESS = "claude-code"


class ReadOnlyRoutingError(RuntimeError):
    """No harness that applies agent definitions is available for a read-only role (#323)."""


DEFAULT_HYBRID_MATRIX: Dict[str, Dict[str, str]] = {
    # rev/review/research route to claude-code in EVERY host row (issue #323):
    # agy headless ignores `--agent`, so routing them to antigravity ran the
    # unrestricted default agent under the reviewer's name.
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
        # Pinned to claude-code regardless of host (issue #320/#323 review):
        # the `tools:` restriction this role relies on for its sandbox is a
        # measured, verified-enforced property of claude-code specifically
        # (Read-only agent got NO_BASH_TOOL even under
        # --dangerously-skip-permissions) and NOT of antigravity (a
        # Read-only agent there still got RAN_BASH under the same flag,
        # tracked as issue #323). This must not drift with the host or with
        # DEFAULT_HYBRID_MATRIX's normal per-host specialization.
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
}


def find_repo_root(start: Optional[Path] = None) -> Path:
    """Find repository root by walking upwards looking for .git or core/harnesses."""
    d = (start or Path.cwd()).resolve()
    for _ in range(8):
        if (d / ".git").exists() or ((d / "core").is_dir() and (d / "harnesses").is_dir()):
            return d
        parent = d.parent
        if parent == d:
            break
        d = parent
    return (start or Path.cwd()).resolve()


def find_team_toml(custom_path: Optional[str] = None, start_dir: Optional[Path] = None) -> Optional[Path]:
    """Locate team.toml from custom override or standard search paths."""
    if custom_path:
        p = Path(custom_path)
        return p if p.is_file() else None
    root = find_repo_root(start_dir)
    candidates = [
        root / ".agents" / "team.toml",
        root / "team.toml",
        root / ".claude" / "team.toml",
    ]
    for c in candidates:
        if c.is_file():
            return c
    return None


def parse_toml(path: Optional[Path]) -> Dict[str, Any]:
    """Parse TOML configuration file with stdlib tomllib or fallback parser."""
    if not path or not path.is_file():
        return {}
    try:
        import tomllib
        with open(path, "rb") as f:
            return tomllib.load(f)
    except Exception:
        pass

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
        res: Dict[str, Any] = {}
        current_section = res
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("[") and line.endswith("]"):
                sec_name = line[1:-1].strip()
                parts = sec_name.split(".")
                curr = res
                for p in parts:
                    curr = curr.setdefault(p, {})
                current_section = curr
            elif "=" in line:
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.split("#")[0].strip().strip('"').strip("'")
                current_section[k] = v
        return res
    except Exception:
        return {}


def detect_native_harness(
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> str:
    """Detect current native harness from environment variables or available binaries."""
    environ = os.environ if env is None else env
    if "ANTIGRAVITY_CONVERSATION_ID" in environ or "AGY_PLUGIN_ROOT" in environ or "AGY_PROJECT_DIR" in environ:
        return "antigravity"
    if "COPILOT_PLUGIN_DATA" in environ or "COPILOT_PROJECT_DIR" in environ:
        return "copilot"
    if "CLAUDE_PLUGIN_ROOT" in environ or "CLAUDE_PROJECT_DIR" in environ:
        return "claude-code"

    # Check available binaries in PATH
    if which_fn("claude"):
        return "claude-code"
    if which_fn("agy"):
        return "antigravity"
    if which_fn("copilot"):
        return "copilot"

    return "claude-code"


def build_harness_command(harness: str, role: str, prompt: str) -> List[str]:
    """Build CLI execution command for target harness."""
    mapped_agent = ROLE_TO_AGENT.get(role, role)
    if harness == "claude-code":
        return ["claude", "--dangerously-skip-permissions", "--agent", mapped_agent, "-p", prompt]
    elif harness == "antigravity":
        # --dangerously-skip-permissions stays unconditional here for now; whether
        # it should become opt-in per call site is a separate decision (#323).
        # Note agy headless does not load `--agent` either (#323), so this flag
        # applies to the stock default agent, not the named mARC specialist.
        return ["agy", "--dangerously-skip-permissions", "--agent", mapped_agent, "-p", prompt]
    elif harness == "copilot":
        return ["copilot", "--prompt", prompt]
    else:
        raise ValueError(f"Unsupported harness: {harness!r}")


def resolve_target_harness(
    host_harness: str,
    role: str,
    explicit_harness: Optional[str] = "auto",
    toml_routes: Optional[Dict[str, str]] = None,
    toml_mode: Optional[str] = None,
    cli_checker: Optional[Callable[[str], Any]] = None,
) -> Tuple[str, bool, Optional[str]]:
    """Resolve target harness based on priority hierarchy and check CLI availability (origin: #241).

    Priorities:
        1. Explicit --harness flag (if not 'auto' or unset).
        2. User explicit route in team.toml [orchestration.routes].
        3. Default hybrid specialization matrix (DEFAULT_HYBRID_MATRIX).

    Fallback:
        If preferred target CLI is not available on PATH, gracefully falls back to host_harness (native).

    Returns:
        (resolved_harness, fallback_occurred, fallback_reason)
    """
    canonical_role = CANONICAL_ROLES.get(role, role)

    # Priority 1: Explicit --harness argument
    if explicit_harness and explicit_harness != "auto":
        target_harness = host_harness if explicit_harness == "native" else explicit_harness
    elif toml_mode == "native":
        target_harness = host_harness
    else:
        # Priority 2: User explicit route in team.toml [orchestration.routes]
        user_route = None
        if isinstance(toml_routes, dict):
            user_route = toml_routes.get(canonical_role) or toml_routes.get(role)

        if user_route and user_route != "auto":
            target_harness = host_harness if user_route == "native" else user_route
        else:
            # Priority 3: When route is 'auto', unset, or in hybrid mode, lookup DEFAULT_HYBRID_MATRIX
            matrix = DEFAULT_HYBRID_MATRIX.get(host_harness, DEFAULT_HYBRID_MATRIX.get("claude-code", {}))
            target_harness = matrix.get(canonical_role) or matrix.get(role, host_harness)

    if target_harness == "native":
        target_harness = host_harness

    if target_harness not in HARNESS_BINARIES:
        target_harness = "claude-code"

    # Fail closed for read-only roles (#323): no source (explicit --harness,
    # team.toml route, native mode, matrix) may land them on a harness that
    # does not apply the agent definition.
    read_only = canonical_role in READ_ONLY_ROLES
    reroute_reason: Optional[str] = None
    if read_only and target_harness in NON_ENFORCING_HARNESSES:
        reroute_reason = (
            f"role '{role}' is read-only but harness '{target_harness}' does not load mARC "
            f"agent definitions in headless mode (issue #323)"
        )
        sys.stderr.write(
            f"[mARC dispatch] Warning: {reroute_reason}. "
            f"Re-routing to '{READ_ONLY_PREFERRED_HARNESS}'.\n"
        )
        target_harness = READ_ONLY_PREFERRED_HARNESS

    def _is_available(h_or_b: str) -> bool:
        b = HARNESS_BINARIES.get(h_or_b, h_or_b)
        if cli_checker is None:
            return shutil.which(b) is not None
        try:
            res = cli_checker(b)
        except Exception:
            res = None
        if res is None and b != h_or_b:
            try:
                res = cli_checker(h_or_b)
            except Exception:
                res = None
        return bool(res)

    # Check target CLI binary availability
    expected_bin = HARNESS_BINARIES.get(target_harness, target_harness)
    if not _is_available(target_harness):
        fallback_reason = f"CLI binary '{expected_bin}' for harness '{target_harness}' not found on PATH"
        if read_only:
            # Only fall back to a harness that applies agent definitions; never
            # to antigravity, even when it is the host (#323).
            candidates = [
                h for h in (READ_ONLY_PREFERRED_HARNESS, host_harness)
                if h not in NON_ENFORCING_HARNESSES
            ]
            ro_fallback = next((h for h in candidates if _is_available(h)), None)
            if ro_fallback is None:
                raise ReadOnlyRoutingError(
                    f"{fallback_reason}, and no harness that enforces mARC agent definitions "
                    f"is available for read-only role '{role}'. Refusing to dispatch it "
                    f"unrestricted (issue #323)."
                )
            fallback_harness = ro_fallback
        else:
            fallback_harness = host_harness
            if not _is_available(fallback_harness):
                for h in HARNESS_BINARIES:
                    if _is_available(h):
                        fallback_harness = h
                        break

        sys.stderr.write(f"[mARC dispatch] Warning: {fallback_reason}. Falling back to '{fallback_harness}'.\n")
        _warn_if_unenforced(fallback_harness)
        if reroute_reason:
            fallback_reason = f"{reroute_reason}; {fallback_reason}"
        return fallback_harness, True, fallback_reason

    _warn_if_unenforced(target_harness)
    if reroute_reason:
        return target_harness, True, reroute_reason
    return target_harness, False, None


def _warn_if_unenforced(harness: str) -> None:
    """One-line stderr warning when dispatching to a harness that ignores agent definitions."""
    if harness in NON_ENFORCING_HARNESSES:
        sys.stderr.write(
            f"[mARC dispatch] Warning: '{harness}' headless dispatch does not load mARC agent "
            f"definitions (persona/model/tools not applied, issue #323).\n"
        )


def resolve_route(
    role: str,
    requested_harness: str = "auto",
    team_toml_path: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> Tuple[str, bool, Optional[str]]:
    """Resolve target harness and apply fallback if CLI binary is unavailable.

    Delegates to resolve_target_harness with host detection and team.toml config.

    Returns:
        (resolved_harness, fallback_occurred, fallback_reason)
    """
    host_harness = detect_native_harness(env=env, which_fn=which_fn)
    config = parse_toml(team_toml_path)
    orchestration = config.get("orchestration", {}) if isinstance(config, dict) else {}
    toml_mode = orchestration.get("mode")
    toml_routes = orchestration.get("routes")

    return resolve_target_harness(
        host_harness=host_harness,
        role=role,
        explicit_harness=requested_harness,
        toml_routes=toml_routes,
        toml_mode=toml_mode,
        cli_checker=which_fn,
    )


def dispatch(
    role: str,
    prompt: str,
    harness: str = "auto",
    timeout: float = 300.0,
    dry_run: bool = False,
    team_toml: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
    runner_fn: Callable[..., Any] = subprocess.run,
) -> Dict[str, Any]:
    """Resolve and dispatch subagent across harnesses."""
    toml_path = find_team_toml(team_toml)
    try:
        resolved_harness, fallback, fallback_reason = resolve_route(
            role=role,
            requested_harness=harness,
            team_toml_path=toml_path,
            env=env,
            which_fn=which_fn,
        )
    except ReadOnlyRoutingError as e:
        # Fail closed (#323): never dispatch a read-only role unrestricted.
        err_msg = f"[mARC dispatch] Error: {e}"
        sys.stderr.write(err_msg + "\n")
        return {
            "role": role,
            "mapped_agent": ROLE_TO_AGENT.get(role, role),
            "requested_harness": harness,
            "harness": None,
            "command": [],
            "command_str": "",
            "fallback": False,
            "fallback_reason": None,
            "dry_run": dry_run,
            "exit_code": 2,
            "stdout": "",
            "stderr": err_msg,
            "success": False,
            "error": str(e),
            "duration_sec": 0.0,
        }

    cmd = build_harness_command(resolved_harness, role, prompt)
    cmd_str = shlex.join(cmd)

    result: Dict[str, Any] = {
        "role": role,
        "mapped_agent": ROLE_TO_AGENT.get(role, role),
        "requested_harness": harness,
        "harness": resolved_harness,
        "command": cmd,
        "command_str": cmd_str,
        "fallback": fallback,
        "fallback_reason": fallback_reason,
        "dry_run": dry_run,
    }

    if dry_run:
        result.update({
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "success": True,
            "duration_sec": 0.0,
        })
        return result

    start_time = time.time()
    try:
        proc = runner_fn(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        duration = time.time() - start_time
        result.update({
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
            "success": (proc.returncode == 0),
            "duration_sec": round(duration, 3),
        })
    except subprocess.TimeoutExpired as e:
        duration = time.time() - start_time
        stdout = e.stdout.decode("utf-8", errors="replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        stderr = e.stderr.decode("utf-8", errors="replace") if isinstance(e.stderr, bytes) else (e.stderr or "")
        err_msg = f"[mARC dispatch] Error: Execution timed out after {timeout} seconds."
        sys.stderr.write(err_msg + "\n")
        result.update({
            "exit_code": 124,
            "stdout": stdout,
            "stderr": (stderr + ("\n" if stderr else "") + err_msg),
            "success": False,
            "error": f"Timeout after {timeout} seconds",
            "duration_sec": round(duration, 3),
        })
    except Exception as e:
        duration = time.time() - start_time
        err_msg = f"[mARC dispatch] Error executing command: {e}"
        sys.stderr.write(err_msg + "\n")
        result.update({
            "exit_code": 1,
            "stdout": "",
            "stderr": err_msg,
            "success": False,
            "error": str(e),
            "duration_sec": round(duration, 3),
        })

    return result


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Cross-harness subagent delegation and poly-model routing for mARC."
    )
    parser.add_argument(
        "--role",
        required=True,
        help="Specialist role (dev, sre, design, sec, rev, research, engineer, security, review)",
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="Task prompt for the specialist subagent",
    )
    parser.add_argument(
        "--harness",
        default="auto",
        choices=["auto", "native", "claude-code", "antigravity", "copilot"],
        help="Target harness (auto, native, claude-code, antigravity, copilot). Default: auto",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="Execution timeout in seconds. Default: 300",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Format output as JSON",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print resolved command without executing",
    )
    parser.add_argument(
        "--team-toml",
        default=None,
        help="Path to team.toml (default: discovered)",
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    args = parse_args(argv)

    result = dispatch(
        role=args.role,
        prompt=args.prompt,
        harness=args.harness,
        timeout=args.timeout,
        dry_run=args.dry_run,
        team_toml=args.team_toml,
    )

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.dry_run:
            print(result["command_str"])
        else:
            if result.get("stdout"):
                sys.stdout.write(result["stdout"])
            if result.get("stderr"):
                sys.stderr.write(result["stderr"])

    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
