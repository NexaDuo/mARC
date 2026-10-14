#!/usr/bin/env python3
"""Cross-harness subagent delegation and poly-model routing (origin: #239, #241).

Stdlib only (no third-party dependencies). Can be invoked directly or imported:
    python3 dispatch_agent.py --role dev --prompt "Implement issue #123" --dry-run
    python3 dispatch_agent.py --role research --prompt "Investigate auth providers" --json

CLI Options:
    --role <role>         Specialist role (dev, sre, design, sec, rev, research, engineer, security, review).
    --prompt <prompt>     Task prompt with context, acceptance criteria, and constraints.
    --harness <harness>   Target harness (auto, native, claude-code, antigravity, copilot, codex). Default: auto.
    --timeout <seconds>   Subprocess execution timeout in seconds. Default: 300.
    --json                Output result as JSON payload.
    --dry-run             Resolve command and print without executing.
    --team-toml <path>    Optional path to team.toml (default: discovered from repo root).

Detached jobs (#352): start a dispatch without blocking, then wait with one call:
    python3 dispatch_agent.py start --role dev (--prompt <s> | --prompt-file <p>) \\
        [--harness ...] [--timeout ...] [--team-toml ...]
      -> prints one JSON line: job_id, pid, role, harness, result_path. Routing and the
         #323 read-only guard run before anything is spawned (refusal exits 2, no job).
    python3 dispatch_agent.py wait <job_id>... [--timeout S] [--any]
      -> silent while blocking; then one JSON line per job: job_id, status
         (succeeded|failed|timed_out|running|lost), exit_code, duration_sec, result_path.
         Exit 0 all succeeded; 1 any failed/timed_out/lost; 2 usage error or unknown
         job id; 75 timeout reached with job(s) still running (call wait again).
    The full dispatch() result JSON is written atomically to result_path, under
    $(git rev-parse --git-common-dir)/marc/jobs/<job_id>/ (outside every working tree),
    or ${XDG_STATE_HOME:-~/.local/state}/marc/jobs/ outside a git repository.

Routing resolution:
    - Priority 1: Explicit --harness argument (if not 'auto').
    - Priority 2: User explicit route in team.toml [orchestration.routes].
    - Priority 3: Embedded default hybrid matrix (DEFAULT_HYBRID_MATRIX) when auto or unrouted.
    - If target CLI binary is not found on PATH: logs warning to stderr and gracefully
      falls back to host/native harness.
    - --role is normalized (whitespace stripped, one leading '@' dropped, lowercased);
      an unknown role is rejected with exit code 2 and nothing is dispatched (#323).
    - Read-only roles (READ_ONLY_ROLES: rev/research/sec/bulk-reader and aliases) never
      run on a harness that does not apply mARC agent definitions
      (NON_ENFORCING_HARNESSES: antigravity, copilot, codex), whatever the source of the route
      (#323). They are re-routed to claude-code with a stderr diagnostic; if claude-code
      is not available, dispatch fails with exit code 2 instead of running them
      unrestricted. The JSON result's policy_reroute/policy_reason fields mark the
      policy override separately from a missing-CLI fallback.
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
    "codex": "codex",
}

# Read-only roles (canonical names, see CANONICAL_ROLES): their agent definitions
# declare no Write/Edit tool, and the review/security gate relies on that. Issue
# #323: in headless `-p` mode `agy --agent <name>` does not load the named agent
# (it silently falls back to the default agent with every tool), so these roles
# must never be dispatched to antigravity. Aliases resolve through CANONICAL_ROLES.
READ_ONLY_ROLES = frozenset({"rev", "research", "sec", "bulk-reader"})

# Harnesses whose headless dispatch path does NOT apply mARC agent definitions
# (persona, model pin, `tools:` restriction). Issue #323: agy headless ignores
# `--agent` (measured on agy 1.2.9); copilot is invoked as `copilot --prompt`
# with no agent selection at all (see build_harness_command). Codex exec
# likewise has no named-agent selector; filesystem sandbox alone does not prove
# agent discovery or tool restrictions, so it remains excluded (#330).
NON_ENFORCING_HARNESSES = frozenset({"antigravity", "copilot", "codex"})

# Preferred harness for read-only roles: claude-code resolves `--agent` and
# enforces `tools:` even under --dangerously-skip-permissions (measured, #320/#323).
READ_ONLY_PREFERRED_HARNESS = "claude-code"


class ReadOnlyRoutingError(RuntimeError):
    """No harness that applies agent definitions is available for a read-only role (#323)."""


class UnknownRoleError(ValueError):
    """The requested role is not a known mARC specialist role or alias (#323)."""


def normalize_role(role: str) -> str:
    """Normalize a requested role and fail closed on unknown names (#323).

    Strips surrounding whitespace, drops one leading '@' (channel handle form),
    lowercases, then requires the result to be a known role or alias. A near-miss
    spelling of a read-only role (e.g. 'REV', '@rev') must never bypass the
    read-only routing guard, and an unknown role has no agent definition on any
    harness, so it is rejected instead of dispatched.
    """
    norm = (role or "").strip()
    if norm.startswith("@"):
        norm = norm[1:]
    norm = norm.lower()
    if norm not in CANONICAL_ROLES:
        known = ", ".join(sorted(CANONICAL_ROLES))
        raise UnknownRoleError(
            f"unknown role {role!r}; expected one of: {known}. Refusing to dispatch (issue #323)."
        )
    return norm


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
    "codex": {
        "dev": "codex",
        "engineer": "codex",
        "sre": "codex",
        "design": "codex",
        "sec": "claude-code",
        "security": "claude-code",
        "rev": "claude-code",
        "review": "claude-code",
        "research": "claude-code",
        # Bulk-reader must use the only harness with a verified read-only
        # tool boundary until Codex isolation is proven empirically.
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
    if "PLUGIN_ROOT" in environ or "CODEX_PROJECT_DIR" in environ:
        return "codex"

    # Check available binaries in PATH
    if which_fn("claude"):
        return "claude-code"
    if which_fn("agy"):
        return "antigravity"
    if which_fn("copilot"):
        return "copilot"
    if which_fn("codex"):
        return "codex"

    return "claude-code"


def build_harness_command(harness: str, role: str, prompt: str) -> List[str]:
    """Build CLI execution command for target harness."""
    role = normalize_role(role)
    if (role == "bulk-reader" and harness != "claude-code") or (
            harness in ("codex", "antigravity") and CANONICAL_ROLES[role] in READ_ONLY_ROLES):
        # Defense in depth: resolve_route already re-routes read-only roles away
        # from codex/antigravity (#323, #330); never build such a command here.
        raise ReadOnlyRoutingError("read-only role requires the verified Claude Code tool boundary (#323, #330)")
    mapped_agent = ROLE_TO_AGENT[role]
    if harness == "claude-code":
        return ["claude", "--dangerously-skip-permissions", "--agent", mapped_agent, "-p", prompt]
    elif harness == "antigravity":
        # --dangerously-skip-permissions stays unconditional here for now; whether
        # it should become opt-in per call site is a separate decision (#323).
        # The Antigravity plugin ships personas as skills (skills/<name>/SKILL.md),
        # not agents, and agy headless ignores `--agent` anyway (#323). So no
        # `--agent` flag: the prompt tells the stock agent to load the persona skill.
        return ["agy", "--dangerously-skip-permissions", "-p",
                f"Read the '{mapped_agent}' skill instructions before proceeding, "
                f"then act as the mARC {mapped_agent} specialist. {prompt}"]
    elif harness == "copilot":
        return ["copilot", "--prompt", prompt]
    elif harness == "codex":
        # Codex selects configured subagents through the model's Agent tool;
        # `-p` is a profile flag, not an agent selector. State the role in the
        # prompt and use a managed worktree for mutating runs.
        sandbox = "read-only" if role in READ_ONLY_ROLES else "workspace-write"
        return ["codex", "exec", "--worktree", "--sandbox", sandbox,
                f"Act as the mARC {mapped_agent} specialist. {prompt}"]
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
    """Backward-compatible tuple form of resolve_target_harness_detailed (origin: #241).

    Returns:
        (resolved_harness, fallback_occurred, fallback_reason)
    """
    d = resolve_target_harness_detailed(
        host_harness, role, explicit_harness, toml_routes, toml_mode, cli_checker,
    )
    return d["harness"], d["fallback"], d["fallback_reason"]


def resolve_target_harness_detailed(
    host_harness: str,
    role: str,
    explicit_harness: Optional[str] = "auto",
    toml_routes: Optional[Dict[str, str]] = None,
    toml_mode: Optional[str] = None,
    cli_checker: Optional[Callable[[str], Any]] = None,
) -> Dict[str, Any]:
    """Resolve target harness based on priority hierarchy and check CLI availability (origin: #241).

    Priorities:
        1. Explicit --harness flag (if not 'auto' or unset).
        2. User explicit route in team.toml [orchestration.routes].
        3. Default hybrid specialization matrix (DEFAULT_HYBRID_MATRIX).

    Fallback:
        If preferred target CLI is not available on PATH, gracefully falls back to host_harness (native).

    Read-only roles (#323) never resolve to a NON_ENFORCING_HARNESSES entry:
    they are re-routed to claude-code, and ReadOnlyRoutingError is raised when
    no enforcing harness is available. Unknown roles raise UnknownRoleError.

    Returns a dict:
        harness          resolved harness
        fallback         True on any deviation from the requested route (CLI missing
                         OR #323 policy re-route); kept for backward compatibility
        fallback_reason  combined human-readable reason (or None)
        policy_reroute   True only when the #323 read-only policy overrode the route
        policy_reason    the #323 policy reason (or None)
        cli_fallback     True only when a missing CLI binary forced a fallback
    """
    role = normalize_role(role)
    canonical_role = CANONICAL_ROLES[role]

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
    if read_only and (target_harness in NON_ENFORCING_HARNESSES or
                      (canonical_role == "bulk-reader" and target_harness != "claude-code")):
        reroute_reason = (
            f"role '{role}' is read-only but harness '{target_harness}' does not apply mARC "
            f"agent definitions in headless dispatch (issue #323)"
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
            # to a NON_ENFORCING_HARNESSES entry, even when it is the host (#323).
            candidates = [
                h for h in (READ_ONLY_PREFERRED_HARNESS, host_harness)
                if h not in NON_ENFORCING_HARNESSES
                and (canonical_role != "bulk-reader" or h == "claude-code")
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
        combined = f"{reroute_reason}; {fallback_reason}" if reroute_reason else fallback_reason
        return {
            "harness": fallback_harness,
            "fallback": True,
            "fallback_reason": combined,
            "policy_reroute": reroute_reason is not None,
            "policy_reason": reroute_reason,
            "cli_fallback": True,
        }

    _warn_if_unenforced(target_harness)
    return {
        "harness": target_harness,
        "fallback": reroute_reason is not None,
        "fallback_reason": reroute_reason,
        "policy_reroute": reroute_reason is not None,
        "policy_reason": reroute_reason,
        "cli_fallback": False,
    }


def _warn_if_unenforced(harness: str) -> None:
    """One-line stderr warning when dispatching to a harness that ignores agent definitions."""
    if harness in NON_ENFORCING_HARNESSES:
        sys.stderr.write(
            f"[mARC dispatch] Warning: '{harness}' headless dispatch does not apply mARC agent "
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

    Returns:
        (resolved_harness, fallback_occurred, fallback_reason)
    """
    d = resolve_route_detailed(role, requested_harness, team_toml_path, env, which_fn)
    return d["harness"], d["fallback"], d["fallback_reason"]


def resolve_route_detailed(
    role: str,
    requested_harness: str = "auto",
    team_toml_path: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> Dict[str, Any]:
    """Like resolve_route, but returns resolve_target_harness_detailed's dict.

    Delegates to resolve_target_harness_detailed with host detection and team.toml config.
    """
    host_harness = detect_native_harness(env=env, which_fn=which_fn)
    config = parse_toml(team_toml_path)
    orchestration = config.get("orchestration", {}) if isinstance(config, dict) else {}
    toml_mode = orchestration.get("mode")
    toml_routes = orchestration.get("routes")

    return resolve_target_harness_detailed(
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
    result = prepare_dispatch(
        role=role, prompt=prompt, harness=harness, dry_run=dry_run,
        team_toml=team_toml, env=env, which_fn=which_fn,
    )
    if "exit_code" in result:
        # Fail-closed refusal (#323) or dry run: nothing to execute.
        return result
    return execute_prepared(result, timeout=timeout, runner_fn=runner_fn)


def prepare_dispatch(
    role: str,
    prompt: str,
    harness: str = "auto",
    dry_run: bool = False,
    team_toml: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
) -> Dict[str, Any]:
    """Resolve the route and build the command, without running it (#352).

    Shared by dispatch() and the `start` subcommand, so routing, the read-only
    fail-closed guard (#323) and CLI fallback are one code path. A returned
    dict that already carries `exit_code` is terminal (refusal or dry run);
    otherwise pass it to execute_prepared().
    """
    toml_path = find_team_toml(team_toml)
    try:
        role = normalize_role(role)
        route = resolve_route_detailed(
            role=role,
            requested_harness=harness,
            team_toml_path=toml_path,
            env=env,
            which_fn=which_fn,
        )
    except (ReadOnlyRoutingError, UnknownRoleError) as e:
        # Fail closed (#323): never dispatch a read-only role unrestricted, and
        # never dispatch an unknown role.
        err_msg = f"[mARC dispatch] Error: {e}"
        sys.stderr.write(err_msg + "\n")
        return {
            "role": role,
            "mapped_agent": ROLE_TO_AGENT.get(role),
            "requested_harness": harness,
            "harness": None,
            "command": [],
            "command_str": "",
            "fallback": False,
            "fallback_reason": None,
            "policy_reroute": False,
            "policy_reason": None,
            "dry_run": dry_run,
            "exit_code": 2,
            "stdout": "",
            "stderr": err_msg,
            "success": False,
            "error": str(e),
            "duration_sec": 0.0,
        }

    resolved_harness = route["harness"]
    cmd = build_harness_command(resolved_harness, role, prompt)
    cmd_str = shlex.join(cmd)

    result: Dict[str, Any] = {
        "role": role,
        "mapped_agent": ROLE_TO_AGENT[role],
        "requested_harness": harness,
        "harness": resolved_harness,
        "command": cmd,
        "command_str": cmd_str,
        # `fallback` keeps its original meaning for existing consumers (any
        # deviation from the requested route); `policy_reroute`/`policy_reason`
        # isolate the #323 read-only override from a missing-CLI fallback.
        "fallback": route["fallback"],
        "fallback_reason": route["fallback_reason"],
        "policy_reroute": route["policy_reroute"],
        "policy_reason": route["policy_reason"],
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
    return result


def execute_prepared(
    result: Dict[str, Any],
    timeout: float = 300.0,
    runner_fn: Callable[..., Any] = subprocess.run,
) -> Dict[str, Any]:
    """Run a command prepared by prepare_dispatch() and record its outcome."""
    result = dict(result)
    cmd = result["command"]
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


# ---------------------------------------------------------------------------
# Detached jobs: `start` / `wait` (#352)
#
# `start` resolves the route in-process (same prepare_dispatch() code path, so
# the #323 read-only guard refuses before anything is spawned), then launches a
# detached runner (`_run <job_dir>`) that executes the prepared command and
# writes result.json atomically. `wait` blocks silently until the job(s)
# finish or its timeout elapses, then prints one compact JSON line per job.
#
# Job state lives under `$(git rev-parse --git-common-dir)/marc/jobs/<job_id>/`
# (shared by every worktree of the clone, never in a working tree), or under
# `${XDG_STATE_HOME:-~/.local/state}/marc/jobs/` outside a git repository.
# ---------------------------------------------------------------------------

# wait exit codes. 75 is EX_TEMPFAIL from sysexits.h: "try again later".
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_STILL_RUNNING = 75

JOB_STATUSES = ("succeeded", "failed", "timed_out", "running", "lost")
_JOB_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
DEFAULT_WAIT_TIMEOUT = 25.0


def jobs_root(cwd: Optional[Path] = None) -> Path:
    """Directory holding job state, outside any working tree (#352)."""
    base = (cwd or Path.cwd()).resolve()
    try:
        proc = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=10,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            common = Path(proc.stdout.strip())
            if not common.is_absolute():
                common = base / common
            return common.resolve() / "marc" / "jobs"
    except (OSError, subprocess.SubprocessError):
        pass
    state = os.environ.get("XDG_STATE_HOME") or str(Path.home() / ".local" / "state")
    return Path(state) / "marc" / "jobs"


def _valid_job_id(job_id: str) -> bool:
    return bool(job_id) and len(job_id) <= 64 and set(job_id) <= _JOB_ID_CHARS


def _new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + os.urandom(4).hex()


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON via temp file + fsync + rename, so readers never see a partial file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(path))
    except BaseException:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise


def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _pid_alive(pid: Any, job_id: str) -> bool:
    """True if the runner process still exists (zombies count as dead).

    On Linux, also require the job id on the process command line, so a
    recycled pid is not mistaken for the runner.
    """
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    except OSError:
        return False
    proc_dir = Path("/proc") / str(pid)
    if proc_dir.is_dir():
        try:
            stat = (proc_dir / "stat").read_text(encoding="utf-8", errors="replace")
            if stat.rsplit(")", 1)[-1].split()[0] in ("Z", "X"):
                return False
            cmdline = (proc_dir / "cmdline").read_bytes().decode("utf-8", errors="replace")
            return job_id in cmdline
        except (OSError, IndexError):
            return False
    return True


def _status_of_result(result: Dict[str, Any]) -> str:
    if result.get("success"):
        return "succeeded"
    if result.get("exit_code") == 124 and str(result.get("error", "")).startswith("Timeout"):
        return "timed_out"
    return "failed"


def start_job(
    role: str,
    prompt: str,
    harness: str = "auto",
    timeout: float = 300.0,
    team_toml: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Resolve the route and launch a detached runner. Returns (exit_code, summary)."""
    prepared = prepare_dispatch(role=role, prompt=prompt, harness=harness, team_toml=team_toml)
    if "exit_code" in prepared:
        # Fail closed (#323) or unknown role: no job is created, nothing runs.
        return prepared["exit_code"], {"error": prepared.get("error"), "exit_code": prepared["exit_code"]}

    workdir = (cwd or Path.cwd()).resolve()
    root = jobs_root(workdir)
    job_id = _new_job_id()
    job_dir = root / job_id
    job_dir.mkdir(parents=True, mode=0o700)
    result_path = job_dir / "result.json"
    _write_json_atomic(job_dir / "spec.json", {
        "job_id": job_id,
        "timeout": timeout,
        "cwd": str(workdir),
        "prepared": prepared,
    })
    log = open(job_dir / "runner.log", "ab")
    try:
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), "_run", str(job_dir)],
            cwd=str(workdir),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            close_fds=True,
            start_new_session=True,
        )
    finally:
        log.close()
    summary = {
        "job_id": job_id,
        "pid": proc.pid,
        "role": prepared["role"],
        "harness": prepared["harness"],
        "result_path": str(result_path),
    }
    _write_json_atomic(job_dir / "job.json", dict(summary, started_at=time.time(), timeout=timeout))
    return EXIT_OK, summary


def run_job(job_dir: Path) -> int:
    """Detached runner body: execute the prepared command, write result.json atomically."""
    spec = _read_json(job_dir / "spec.json") or {}
    result_path = job_dir / "result.json"
    prepared = spec.get("prepared")
    try:
        if not isinstance(prepared, dict) or not prepared.get("command"):
            raise RuntimeError("job spec missing or unreadable")
        if prepared["command"][0] not in HARNESS_BINARIES.values():
            # Only ever run a harness CLI that prepare_dispatch() could have built.
            raise RuntimeError(f"job spec command {prepared['command'][0]!r} is not a harness CLI")
        result = execute_prepared(prepared, timeout=float(spec.get("timeout", 300.0)))
    except Exception as e:  # never leave a job without a result
        result = dict(prepared or {}, exit_code=1, stdout="", stderr=str(e),
                      success=False, error=str(e), duration_sec=0.0)
    result["job_id"] = spec.get("job_id", job_dir.name)
    result["status"] = _status_of_result(result)
    _write_json_atomic(result_path, result)
    return EXIT_OK


def job_summary(job_id: str, root: Path) -> Dict[str, Any]:
    """Compact status for one job: never includes command/stdout (#352)."""
    job_dir = root / job_id
    result_path = job_dir / "result.json"
    result = _read_json(result_path)
    if result is None:
        meta = _read_json(job_dir / "job.json") or {}
        if _pid_alive(meta.get("pid"), job_id):
            started = meta.get("started_at")
            elapsed = round(time.time() - started, 3) if isinstance(started, (int, float)) else None
            return {"job_id": job_id, "status": "running", "exit_code": None,
                    "duration_sec": elapsed, "result_path": str(result_path)}
        # The runner may have written its result between the two checks.
        result = _read_json(result_path)
        if result is None:
            return {"job_id": job_id, "status": "lost", "exit_code": None,
                    "duration_sec": None, "result_path": str(result_path)}
    return {
        "job_id": job_id,
        "status": result.get("status") or _status_of_result(result),
        "exit_code": result.get("exit_code"),
        "duration_sec": result.get("duration_sec"),
        "result_path": str(result_path),
    }


def wait_jobs(
    job_ids: List[str],
    timeout: float = DEFAULT_WAIT_TIMEOUT,
    any_mode: bool = False,
    root: Optional[Path] = None,
) -> Tuple[int, List[Dict[str, Any]]]:
    """Block (silently) until the jobs finish or `timeout` elapses (#352).

    Exit code: 0 all waited jobs succeeded (with --any: at least one finished,
    and every finished one succeeded); 1 any finished job failed, timed out or
    was lost; 75 timeout reached with job(s) still running and none failed.
    """
    root = root or jobs_root()
    deadline = time.monotonic() + max(0.0, timeout)
    interval = 0.1
    while True:
        summaries = [job_summary(j, root) for j in job_ids]
        running = [s for s in summaries if s["status"] == "running"]
        done = [s for s in summaries if s["status"] != "running"]
        if not running or (any_mode and done) or time.monotonic() >= deadline:
            break
        time.sleep(min(interval, max(0.0, deadline - time.monotonic())))
        interval = min(interval * 2, 1.0)
    if any(s["status"] != "succeeded" for s in done):
        return EXIT_FAILED, summaries
    if running and not (any_mode and done):
        return EXIT_STILL_RUNNING, summaries
    return EXIT_OK, summaries


def _compact(data: Dict[str, Any]) -> str:
    return json.dumps(data, separators=(",", ":"))


def _main_start(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="dispatch_agent.py start",
        description="Launch a detached specialist dispatch and print its job id (#352).",
    )
    parser.add_argument("--role", required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prompt")
    group.add_argument("--prompt-file")
    parser.add_argument("--harness", default="auto",
                        choices=["auto", "native", "claude-code", "antigravity", "copilot", "codex"])
    parser.add_argument("--timeout", type=float, default=300.0,
                        help="Harness execution timeout in seconds. Default: 300")
    parser.add_argument("--team-toml", default=None)
    args = parser.parse_args(argv)
    if args.prompt_file is not None:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as e:
            sys.stderr.write(f"[mARC dispatch] Error: cannot read --prompt-file: {e}\n")
            return EXIT_USAGE
    else:
        prompt = args.prompt
    code, summary = start_job(args.role, prompt, args.harness, args.timeout, args.team_toml)
    if code == EXIT_OK:
        print(_compact(summary))
    return code


def _main_wait(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="dispatch_agent.py wait",
        description=("Block until dispatch job(s) finish. Exit 0 all succeeded, 1 any failed/"
                     "timed_out/lost, 2 usage error or unknown job, 75 still running: call wait again."),
    )
    parser.add_argument("job_ids", nargs="+")
    parser.add_argument("--timeout", type=float, default=DEFAULT_WAIT_TIMEOUT,
                        help=f"Seconds to block before returning 75. Default: {DEFAULT_WAIT_TIMEOUT:g}")
    parser.add_argument("--any", action="store_true", help="Return as soon as one job finishes")
    args = parser.parse_args(argv)
    root = jobs_root()
    for j in args.job_ids:
        if not _valid_job_id(j) or not (root / j).is_dir():
            sys.stderr.write(f"[mARC dispatch] Error: unknown job id {j!r} under {root}\n")
            return EXIT_USAGE
    code, summaries = wait_jobs(args.job_ids, args.timeout, args.any, root)
    for s in summaries:
        print(_compact(s))
    return code


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
        choices=["auto", "native", "claude-code", "antigravity", "copilot", "codex"],
        help="Target harness (auto, native, claude-code, antigravity, copilot, codex). Default: auto",
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
    if argv and argv[0] == "start":
        return _main_start(argv[1:])
    if argv and argv[0] == "wait":
        return _main_wait(argv[1:])
    if argv and argv[0] == "_run" and len(argv) == 2:
        return run_job(Path(argv[1]))
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
