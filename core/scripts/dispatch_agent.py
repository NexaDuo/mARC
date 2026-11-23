#!/usr/bin/env python3
"""Cross-harness subagent delegation and poly-model routing (origin: #239, #241).

Stdlib only (no third-party dependencies). Can be invoked directly or imported:
    python3 dispatch_agent.py --role dev --prompt "Implement issue #123" --dry-run
    python3 dispatch_agent.py --role research --prompt "Investigate auth providers" --json

CLI Options:
    --role <role>         Specialist role (dev, sre, design, sec, rev, research, arch, engineer, security, review, architect).
    --prompt <prompt>     Task prompt with context, acceptance criteria, and constraints.
    --harness <harness>   Target harness (auto, native, claude-code, antigravity, copilot, codex). Default: auto.
    --timeout <seconds>   Subprocess execution timeout in seconds. Default: 300; 1800 for Codex
                          writer runs (override: team.toml [orchestration] codex_timeout_sec).
    --json                Output result as JSON payload.
    --dry-run             Resolve command and print without executing.
    --team-toml <path>    Optional path to team.toml (default: discovered from repo root).

Detached jobs (#352): start a dispatch without blocking, then wait with one call:
    python3 dispatch_agent.py start --role dev (--prompt <s> | --prompt-file <p>) \\
        [--harness ...] [--timeout ...] [--team-toml ...]
      -> prints one JSON line: job_id, pid, role, harness, result_path. Routing and the
         #323 read-only guard run before anything is spawned (refusal exits 2, no job).
    python3 dispatch_agent.py wait <job_id>... [--timeout S] [--any] [--store DIR]
      -> silent while blocking; then one JSON line per job: job_id, status
         (succeeded|failed|timed_out|running|lost), exit_code, duration_sec, result_path.
         Exit 0 all succeeded; 1 any failed/timed_out/lost (others may still be
         running); 2 usage error, unknown job id or unusable job store; 75 timeout
         reached with job(s) still running (call wait again).
    The full dispatch() result JSON is written atomically to result_path, under
    $(git rev-parse --git-common-dir)/marc/jobs/<job_id>/ (outside every working tree),
    or ${XDG_STATE_HOME:-~/.local/state}/marc/jobs/ outside a git repository.
    Liveness is an flock the runner holds for its lifetime, not a pid lookup, so a
    `wait` in another PID namespace (e.g. a sandbox) still sees a live job as running.

Task pipeline (#361): one task end to end, without the operator in the loop:
    python3 dispatch_agent.py task <name> <branch> --prompt P --title T --body B --commit-msg M
    python3 dispatch_agent.py task-usage [--task <name>]
  See task_pipeline.py for the steps, the exit codes (0-6) and team.toml [task].

Routing resolution:
    - Priority 1: Explicit --harness argument (if not 'auto').
    - Priority 2: User explicit route in team.toml [orchestration.routes].
    - Priority 3: Embedded default hybrid matrix (DEFAULT_HYBRID_MATRIX) when auto or unrouted.
    - If target CLI binary is not found on PATH: logs warning to stderr and gracefully
      falls back to host/native harness.
    - --role is normalized (whitespace stripped, one leading '@' dropped, lowercased);
      an unknown role is rejected with exit code 2 and nothing is dispatched (#323).
    - Read-only roles (READ_ONLY_ROLES: rev/research/sec/arch/bulk-reader and aliases) never
      run on a harness that does not apply mARC agent definitions
      (NON_ENFORCING_HARNESSES: antigravity, copilot, codex), whatever the source of the route
      (#323). They are re-routed to claude-code with a stderr diagnostic; if claude-code
      is not available, dispatch fails with exit code 2 instead of running them
      unrestricted. The JSON result's policy_reroute/policy_reason fields mark the
      policy override separately from a missing-CLI fallback.
    - Codex read-only roles: opt-in, fs read-only + network profile, verified by the CI
      probe (origin: #351 · 2026-10-14). sec/rev/research (never bulk-reader) may run
      on codex only when team.toml [orchestration.routes] names "codex" for that role.
      `--harness codex` alone is not an opt-in (it is honored only together with that
      route); auto, native, host detection and fallbacks never pick codex for them.
      The argv is fixed: `codex exec -C <checkout>` plus the CODEX_READONLY_PROFILE_ARGS
      permissions profile (whole filesystem read, network on), the role's agent
      definition prepended to the prompt, and the effort/model pin from the Codex agent
      descriptor. Never bypass, --full-auto, workspace-write or danger-full-access.
      Secrets stay readable and a networked reviewer can still run `gh pr merge`,
      exactly as Claude Code reviewers can; only server-side branch protection
      prevents that. scripts/probe_codex_readonly.py checks what the profile contains
      under `codex sandbox -P` (no model); it does not prove that `codex exec` selects
      it via `default_permissions` over a user or project `sandbox_mode`, which stays
      unverified until the owner's `--exec` run passes. The opt-in is read from the
      dispatching checkout's team.toml (a PR can change its own routing). It counts
      only from the team.toml discovered from that checkout: sync dispatch, `start`
      and the runner's re-check all re-discover it there (`mode` included) and refuse
      an opt-in that comes from any other --team-toml, inside or outside the checkout.
    - Model pins (origin: #385 · 2026-11-10). Codex writer runs carry the persona
      `model`/`model_reasoning_effort` from the Codex descriptor (`-m`, `-c`), like the
      read-only path. Antigravity runs carry `--model <slug>` from
      `agent-configs/antigravity/<agent>.toml` (no descriptor: no flag; never `--effort`,
      the slug carries the effort). Consumer overrides come from the team.toml
      discovered from the checkout: [orchestration.codex_profiles] (role -> Codex
      profile; a set profile replaces both persona pins, so the profile's own model and
      effort win; writer roles only) and [orchestration.antigravity_models] (role ->
      slug). A malformed pin or profile fails closed; an unknown agy slug makes agy exit
      non-zero, and that exit is reported as is.
    - Codex writer runs on real apps (origin: #401 · 2026-11-22). Default timeout is 1800 s
      (300 s for everything else); team.toml [orchestration] codex_timeout_sec (at most
      7200) changes it and `--timeout` beats both. Opt-in sandbox widening, both off by
      default, is NOT read from the checkout (a PR could widen its own sandbox): only from
      the operator's `${XDG_CONFIG_HOME:-~/.config}/marc/codex.toml`, per repo under
      `[repos."<abs repo top-level>"]`: codex_writable_dirs (existing directories, passed
      as `--add-dir`; home, roots, system trees, credential dirs and the git dir are
      refused) and codex_network_access (`-c sandbox_workspace_write.network_access=true`).
      A team.toml that sets them is ignored with a warning. The result JSON reports both
      and their source. On a timeout the sync path kills the Codex process group and the
      Codex worktree is kept; the result carries `partial_work` for the one attributable
      Codex-managed worktree created during the run (else unattributed `candidates`).
      The `task` pipeline does not use this resolution.
"""
from __future__ import annotations

import argparse
import atexit
import json
import math
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

try:
    import fcntl
except ImportError:  # non-POSIX: start/wait report a clean error
    fcntl = None  # type: ignore[assignment]

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
    "arch": "architect",  # read-only advisor (#388)
    "architect": "architect",
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
    "arch": "arch",
    "architect": "arch",
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
READ_ONLY_ROLES = frozenset({"rev", "research", "sec", "arch", "bulk-reader"})

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

# Codex read-only reviewers (origin: #351 · 2026-10-14). The read-only roles in
# this set may run on codex, but ONLY when team.toml [orchestration.routes]
# names "codex" for that role explicitly (see codex_readonly_opt_in). auto,
# host detection, native mode and every fallback still never pick codex for
# them. bulk-reader is deliberately absent: it stays pinned to claude-code.
# The boundary is a fixed Codex permissions profile: whole filesystem
# read-only, network on (parity with Claude Code reviewers, which already have
# Bash + WebFetch). Its contents are probed in CI by scripts/probe_codex_readonly.py
# via `codex sandbox -P`; selection by `codex exec` is owner-verified (--exec). Secrets stay readable, and no local sandbox stops a
# networked `gh pr merge`: that is server-side branch protection's job.
CODEX_READONLY_ROLES = frozenset({"rev", "research", "sec"})
CODEX_READONLY_PROFILE = "marc_readonly"
CODEX_READONLY_PROFILE_ARGS: Tuple[str, ...] = (
    "-c", f'default_permissions="{CODEX_READONLY_PROFILE}"',
    "-c", f'permissions.{CODEX_READONLY_PROFILE}.filesystem={{":root"="read"}}',
    "-c", f"permissions.{CODEX_READONLY_PROFILE}.network.enabled=true",
)
# Verdict line required as the last line of a Codex reviewer's final response,
# so the dual-marker merge gate keeps a grep-verifiable result (#351).
CODEX_READONLY_VERDICT_ROLES = frozenset({"rev", "sec"})
# Values Codex accepts for model_reasoning_effort (#351 SEC-3); anything else fails closed.
# `max` (#385) is the documented first escalation step for a Codex reviewer.
CODEX_REASONING_EFFORTS = frozenset({"minimal", "low", "medium", "high", "xhigh", "max"})
_CODEX_MODEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_:/")
# An `agy --model` slug (#385): a plain lowercase id, the effort suffix included
# (e.g. "<family>-high"); never option-shaped.
_AGY_MODEL_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,99}")
# A Codex config profile name (`codex exec -p <name>`), same rule as task_codex_profile.
_CODEX_PROFILE_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")


class ReadOnlyRoutingError(RuntimeError):
    """No harness that applies agent definitions is available for a read-only role (#323)."""


class ModelPinError(ValueError):
    """A model pin or profile from a descriptor or team.toml is not a plain id (#385)."""


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


def codex_readonly_opt_in(role: str, toml_routes: Any) -> bool:
    """Does team.toml [orchestration.routes] explicitly route this read-only role to codex? (#351)

    Only the literal value "codex" counts: "auto", "native" or a missing entry
    never opt in, even when the host happens to be codex. The lookup mirrors
    route resolution (canonical key first, then the alias that was requested).
    """
    norm = normalize_role(role)
    canonical = CANONICAL_ROLES[norm]
    if canonical not in CODEX_READONLY_ROLES or not isinstance(toml_routes, dict):
        return False
    route = toml_routes.get(canonical) or toml_routes.get(norm)
    return isinstance(route, str) and route.strip() == "codex"


def is_forbidden_readonly_route(role: str, harness: Optional[str], codex_opt_in: bool = False) -> bool:
    """Single #323 predicate: may this (role, harness) pair NOT run?

    True when the role is read-only and the harness does not apply mARC agent
    definitions headless (NON_ENFORCING_HARNESSES), or when the role is
    bulk-reader and the harness is not the verified claude-code boundary.
    Used by route resolution, command building and the detached runner (#352).

    One narrow exception (origin: #351 · 2026-10-14): a role in
    CODEX_READONLY_ROLES may run on codex when `codex_opt_in` is True, which
    callers set only from codex_readonly_opt_in() on the team.toml routes.
    The default (False) keeps the #323/#330 fail-closed behavior.
    """
    canonical = CANONICAL_ROLES[normalize_role(role)]
    if canonical not in READ_ONLY_ROLES:
        return False
    if harness == "codex" and codex_opt_in and canonical in CODEX_READONLY_ROLES:
        return False
    if harness in NON_ENFORCING_HARNESSES:
        return True
    return canonical == "bulk-reader" and harness != READ_ONLY_PREFERRED_HARNESS


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
        "arch": "claude-code",
        "architect": "claude-code",
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
        "arch": "claude-code",
        "architect": "claude-code",
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
        "arch": "claude-code",
        "architect": "claude-code",
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
        "arch": "claude-code",
        "architect": "claude-code",
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


def _plugin_dir() -> Path:
    """Directory holding this script's sibling `agents/` (plugin root, or `core/`)."""
    return Path(__file__).resolve().parent.parent


def _strip_front_matter(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                return "\n".join(lines[i + 1:]).strip()
    return text.strip()


def load_codex_persona(mapped_agent: str, plugin_dir: Optional[Path] = None) -> Tuple[str, Dict[str, str]]:
    """Agent-definition body plus Codex pins for a read-only role on codex (#351).

    `codex exec` has no agent selector, so the persona travels in the prompt
    (as the antigravity path names its persona skill in the prompt). The body
    is the shipped `agents/<agent>.md` next to this script, front matter
    stripped (`skills/<agent>/SKILL.md` in the Antigravity plugin). Pins come
    only from the Codex agent descriptor via load_codex_pins
    (`agents/<agent>.toml` in the Codex plugin, else
    `agent-configs/codex/<agent>.toml`, which `core/` and every compiled
    plugin ship since #385): `model` -> `-m`, `model_reasoning_effort` -> `-c`.
    A checkout without that descriptor (e.g. a stripped copy) passes no pin,
    and Codex uses its configured default. The `.md`
    front matter's `model:` names a Claude model and is never passed to codex.
    A missing persona fails closed: a reviewer never runs without its contract.
    """
    base = plugin_dir or _plugin_dir()
    md = base / "agents" / f"{mapped_agent}.md"
    if not md.is_file():
        # The Antigravity plugin ships personas as skills (see build_harness_command).
        skill = base / "skills" / mapped_agent / "SKILL.md"
        if skill.is_file():
            md = skill
    try:
        body = _strip_front_matter(md.read_text(encoding="utf-8"))
    except OSError as e:
        raise ReadOnlyRoutingError(
            f"cannot read agent definition {md} for the codex read-only route (#351): {e}") from e
    if not body:
        raise ReadOnlyRoutingError(f"agent definition {md} is empty; refusing codex read-only route (#351)")
    try:
        pins = load_codex_pins(mapped_agent, base)
    except ModelPinError as e:
        raise ReadOnlyRoutingError(f"{e}; refusing codex read-only route (#351)") from e
    return body, pins


def load_codex_pins(mapped_agent: str, plugin_dir: Optional[Path] = None) -> Dict[str, str]:
    """`model`/`model_reasoning_effort` from the Codex descriptor of an agent (#351, #385).

    Looks in `agents/<agent>.toml` (the Codex plugin) then
    `agent-configs/codex/<agent>.toml` (`core/` and every compiled plugin). No
    descriptor or no key: no pin, and Codex uses its configured default.
    Raises ModelPinError for a value that is not allowlisted.
    """
    base = plugin_dir or _plugin_dir()
    pins: Dict[str, str] = {}
    desc: Optional[Path] = None
    for cand in (base / "agents" / f"{mapped_agent}.toml",
                 base / "agent-configs" / "codex" / f"{mapped_agent}.toml"):
        if cand.is_file():
            desc = cand
            data = parse_toml(cand)
            for key in ("model", "model_reasoning_effort"):
                val = data.get(key)
                if isinstance(val, str) and val.strip():
                    pins[key] = val.strip()
            break
    # Allowlist before the value reaches argv (#351 SEC-3): the effort lands
    # inside a TOML `-c` value, and the model must never parse as a flag.
    effort = pins.get("model_reasoning_effort")
    if effort is not None and effort not in CODEX_REASONING_EFFORTS:
        raise ModelPinError(
            f"model_reasoning_effort {effort!r} in {desc} is not one of "
            f"{sorted(CODEX_REASONING_EFFORTS)}")
    model = pins.get("model")
    if model is not None and (model.startswith("-") or not set(model) <= _CODEX_MODEL_CHARS):
        raise ModelPinError(f"model {model!r} in {desc} is not a plain model id (#351)")
    return pins


def load_agy_model(mapped_agent: str, plugin_dir: Optional[Path] = None) -> Optional[str]:
    """The `agy --model` slug from `agent-configs/antigravity/<agent>.toml` (#385).

    None when the role has no descriptor or the descriptor has no `model`
    (agy then uses its own default, as before #385). Raises ModelPinError for
    a slug that is not a plain lowercase id.
    """
    desc = (plugin_dir or _plugin_dir()) / "agent-configs" / "antigravity" / f"{mapped_agent}.toml"
    if not desc.is_file():
        return None
    val = parse_toml(desc).get("model")
    if not isinstance(val, str) or not val.strip():
        return None
    return _check_agy_model(val.strip(), str(desc))


def _check_agy_model(slug: str, source: str) -> str:
    if not _AGY_MODEL_RE.fullmatch(slug):
        raise ModelPinError(f"antigravity model {slug!r} in {source} is not a plain model slug (#385)")
    return slug


DEFAULT_TIMEOUT = 300.0
# Real app tasks (build + test suite) outlast 300 s: field runs stopped at 300 s with
# half-written files and at 1500 s with no diff (#401). Writers only; reviewers keep 300.
CODEX_WRITER_DEFAULT_TIMEOUT = 1800.0

_OVERRIDE_TABLES: Dict[str, str] = {"codex": "codex_profiles", "antigravity": "antigravity_models"}


def load_model_override(harness: str, role: str, cwd: Optional[str] = None) -> Optional[str]:
    """The per-role override for ONE target harness, from the checkout's team.toml (#385).

    codex -> [orchestration.codex_profiles] (a profile name), antigravity ->
    [orchestration.antigravity_models] (a slug); any other harness -> None.
    Only that harness's table is read and validated, so a bad entry in the
    other table neither blocks this dispatch nor shows up in its error
    (PR #387 REV-1); a bad entry in this table fails closed. Same trust anchor
    as the #351 opt-in: only the team.toml discovered from `cwd`, so sync
    dispatch, `start` and the detached runner's re-check build the same argv.
    Lookup mirrors routing (canonical key first, then the requested alias).
    """
    table = _OVERRIDE_TABLES.get(harness)
    if table is None:
        return None
    start = Path(cwd) if cwd else Path.cwd()
    if not start.is_dir():
        return None
    path = find_team_toml(None, start_dir=start)
    config = parse_toml(path)
    orch = config.get("orchestration", {}) if isinstance(config, dict) else {}
    entries = orch.get(table) if isinstance(orch, dict) else None
    if not isinstance(entries, dict):
        return None
    norm = normalize_role(role)
    val = entries.get(CANONICAL_ROLES[norm], entries.get(norm))
    if val is None:
        return None
    if not isinstance(val, str):
        raise ModelPinError(f"[orchestration.{table}] {norm} in {path} must be a string (#385)")
    if harness == "codex":
        if not _CODEX_PROFILE_RE.fullmatch(val):
            raise ModelPinError(
                f"[orchestration.codex_profiles] {norm} = {val!r} in {path} is not a plain profile "
                f"name (letters, digits, '_', '.', '-'; not starting with '-') (#385)")
        return val
    return _check_agy_model(val, f"{path} [orchestration.antigravity_models]")


CODEX_MAX_TIMEOUT_SEC = 7200.0
_CODEX_SANDBOX_KEYS = ("codex_writable_dirs", "codex_network_access")
_CODEX_REPO_KEYS = ("codex_timeout_sec",) + _CODEX_SANDBOX_KEYS
# System trees a writable dir may not be, or be inside (realpath-compared).
_SYSTEM_TREES = ("/etc", "/proc", "/sys", "/dev", "/boot", "/root", "/usr", "/bin", "/sbin",
                 "/lib", "/lib32", "/lib64", "/libx32", "/var", "/run", "/opt", "/mnt", "/media",
                 "/srv")
# Credential and agent-state locations under $HOME a writable dir may not be, or be inside.
_HOME_DENY = (".ssh", ".gnupg", ".aws", ".kube", ".docker", ".azure", ".codex", ".claude",
              ".agents", ".config/gh", ".config/gcloud",
              # Code-execution persistence: PATH dirs, user services, autostart, shell/env hooks.
              ".local/bin", ".local/share/systemd", ".config/systemd", ".config/autostart",
              ".config/environment.d", ".config/fish", ".bashrc.d", ".zshrc.d", ".profile.d")


def _is_within(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def codex_user_config_path() -> Path:
    """Operator-side Codex sandbox config, `${XDG_CONFIG_HOME:-~/.config}/marc/codex.toml` (#401 SEC-1)."""
    base = os.environ.get("XDG_CONFIG_HOME", "")
    if not base or not os.path.isabs(base):
        base = os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "marc" / "codex.toml"


def _orchestration_table(cwd: Optional[str]) -> Tuple[Dict[str, Any], Optional[Path]]:
    """[orchestration] of the team.toml discovered from `cwd` (same trust anchor as the pins)."""
    start = Path(cwd) if cwd else Path.cwd()
    if not start.is_dir():
        return {}, None
    path = find_team_toml(None, start_dir=start)
    config = parse_toml(path)
    orch = config.get("orchestration", {}) if isinstance(config, dict) else {}
    return (orch if isinstance(orch, dict) else {}), path


_FACTS_CACHE: Optional[Dict[str, Tuple[str, Optional[str]]]] = None  # set only by the hook path (#409 SEC-6)
_HOOK_GIT_TIMEOUT = 1.0


def _checkout_facts(cwd: Optional[str]) -> Tuple[str, Optional[str]]:
    """(realpath of the checkout top-level, realpath of its git common dir or None).

    On the SessionStart hook path (`_FACTS_CACHE` set) the two git calls run once with a
    1 s bound each, so the whole hook stays near 3 s even without `timeout(1)` (#409 SEC-6).
    """
    key = cwd or ""
    if _FACTS_CACHE is not None and key in _FACTS_CACHE:
        return _FACTS_CACHE[key]
    git_timeout = _HOOK_GIT_TIMEOUT if _FACTS_CACHE is not None else 20.0
    facts = _checkout_facts_uncached(cwd, git_timeout)
    if _FACTS_CACHE is not None:
        _FACTS_CACHE[key] = facts
    return facts


def _main_checkout(top: str, common: Optional[str]) -> str:
    """The main checkout path: a linked worktree maps to its main checkout (#409 REV-2)."""
    return os.path.dirname(common) if common and os.path.basename(common) == ".git" else top


def _checkout_facts_uncached(cwd: Optional[str], git_timeout: float) -> Tuple[str, Optional[str]]:
    start = Path(cwd) if cwd else Path.cwd()
    top_out = _git_safe(["rev-parse", "--show-toplevel"], cwd=str(start), timeout=git_timeout)
    top = os.path.realpath(top_out.strip() if top_out and top_out.strip() else str(find_repo_root(start)))
    common_out = _git_safe(["rev-parse", "--git-common-dir"], cwd=str(start), timeout=git_timeout)
    common = (os.path.realpath(os.path.join(str(start), common_out.strip()))
              if common_out and common_out.strip() else None)
    return top, common


def _symlink_hazard(text: str) -> Optional[str]:
    """Why a symlink in the lexical path of `text` could have been planted by someone else (#401 SEC-9).

    A link is suspect when it is owned by neither you nor root, or when its parent
    directory is world-writable (anyone could have swapped it, e.g. under /tmp).
    A `..` after a symlink is also refused: lexical and resolved paths would differ.
    """
    if ".." in text.split(os.sep) and os.path.realpath(text) != os.path.realpath(os.path.normpath(text)):
        return "contains '..' after a symlink, so its meaning is ambiguous"
    me = os.getuid() if hasattr(os, "getuid") else None
    cur = os.sep
    for part in [p for p in os.path.normpath(text).split(os.sep) if p]:
        cur = os.path.join(cur, part)
        if not os.path.islink(cur):
            continue
        try:
            owner = os.lstat(cur).st_uid
            parent_mode = os.stat(os.path.dirname(cur)).st_mode
        except OSError:
            return f"has an unreadable symlink component ({cur})"
        if me is not None and owner not in (0, me):
            return f"has a symlink component owned by another user ({cur})"
        if parent_mode & 0o002:
            return f"has a symlink component in a world-writable directory ({cur})"
    return None


def _check_writable_dir(raw: Any, where: str, top: str, common: Optional[str], cfg: Path) -> str:
    """Validate one codex_writable_dirs entry; returns its realpath. Fails closed (#401 SEC-2)."""
    if not isinstance(raw, str) or not raw.strip() or "\0" in raw:
        raise ModelPinError(f"{where}: every entry must be a non-empty string")
    text = os.path.expanduser(raw.strip())
    if text.startswith("-") or not os.path.isabs(text):
        raise ModelPinError(f"{where}: {raw!r} must be an absolute path (a leading '~' is fine)")
    hazard = _symlink_hazard(text)
    if hazard:
        raise ModelPinError(f"{where}: {raw!r} {hazard}; use the real path of the directory")
    real = os.path.realpath(text)
    if not os.path.isdir(real):
        raise ModelPinError(f"{where}: {raw!r} (resolves to {real}) is not an existing directory")
    home_lex = os.path.normpath(os.path.expanduser("~"))
    home = os.path.realpath(home_lex)
    anchors = [top]
    if common and os.path.basename(common) == ".git":
        anchors.append(os.path.dirname(common))
    cfg_dirs = [os.path.realpath(str(cfg)), os.path.realpath(str(cfg.parent))]
    why = None
    if real == os.path.dirname(real):
        why = "is the filesystem root"
    elif _is_within(home, real):
        why = "is your home directory or one of its ancestors"
    elif any(_is_within(anchor, real) for anchor in anchors):
        why = "is the checkout (or its main checkout) or one of their ancestors"
    elif common and _is_within(real, common):
        why = "is inside the repository's git directory"
    elif any(_is_within(c, real) for c in cfg_dirs) or _is_within(real, cfg_dirs[1]):
        why = "contains or is this sandbox config file's directory"
    else:
        for tree in _SYSTEM_TREES:
            if _is_within(real, os.path.realpath(tree)):
                why = f"is a system tree ({tree})"
                break
        else:
            for base in {home_lex, home}:
                for rel in _HOME_DENY + (".config",):
                    deny = os.path.realpath(os.path.join(base, rel))
                    if real == deny or (rel != ".config" and _is_within(real, deny)):
                        why = f"is a credential or agent directory (~/{rel})"
                        break
                if why:
                    break
    if why:
        raise ModelPinError(
            f"{where}: {raw!r} (resolves to {real}) {why}; name a specific toolchain cache "
            f"directory instead")
    return real


def _user_repo_section(cfg: Path, cwd: Optional[str], what: str) -> Tuple[Optional[Dict[str, Any]], str]:
    """The `[repos."<abs path>"]` section of an operator-side file for this checkout (#401, #409).

    Shared validation for every operator-side file (`codex.toml`, `harnesses.toml`): refuses a
    file that resolves inside the checkout, is not owned by you, or is group/world-writable,
    needs tomllib, and matches the checkout top-level or its main checkout (a linked worktree
    matches its main checkout). Returns (section or None, realpath of the top-level).
    """
    if not cfg.is_file():
        return None, ""
    top, common = _checkout_facts(cwd)
    real_cfg = os.path.realpath(str(cfg))
    main_checkout = os.path.dirname(common) if common and os.path.basename(common) == ".git" else None
    if (_is_within(real_cfg, top) or (common and _is_within(real_cfg, common))
            or (main_checkout and _is_within(real_cfg, main_checkout))):
        raise ModelPinError(
            f"{what} {cfg} resolves inside the checkout ({real_cfg}); the checkout "
            f"must not be able to widen its own sandbox. Move it outside the repo (#401)")
    if hasattr(os, "getuid") and os.stat(real_cfg).st_uid != os.getuid():
        raise ModelPinError(f"{what} {cfg} is not owned by you; refusing (#401)")
    if os.stat(real_cfg).st_mode & 0o022:
        raise ModelPinError(
            f"{what} {cfg} is group- or world-writable; refusing so no other user "
            f"can widen your sandbox. Fix with: chmod go-w {shlex.quote(real_cfg)} (#401)")
    try:
        import tomllib
    except ImportError:
        raise ModelPinError(
            f"{cfg} requires Python 3.11+ (tomllib) to be read; upgrade Python or remove the "
            f"file (#401)") from None
    try:
        with open(real_cfg, "rb") as f:
            data = tomllib.load(f)
    except (OSError, ValueError) as e:
        raise ModelPinError(f"cannot parse {cfg}: {e} (#401)") from None
    repos = data.get("repos", {})
    if not isinstance(repos, dict):
        raise ModelPinError(f"[repos] in {cfg} must be a table of per-repo sections (#401)")
    wanted = [top]
    if common and os.path.basename(common) == ".git":
        wanted.append(os.path.dirname(common))
    valid: List[Tuple[str, Dict[str, Any]]] = []
    for key, sec in repos.items():
        if not key.strip() or not os.path.isabs(key) or "\0" in key:
            sys.stderr.write(f"[mARC dispatch] warning: skipping [repos.{key!r}] in {cfg}: the key "
                             f"must be an absolute path (#401)\n")
            continue
        if isinstance(sec, dict):
            valid.append((os.path.realpath(key), sec))
    for target in wanted:
        for real_key, sec in valid:
            if real_key == target:
                return sec, top
    return None, top


def _load_user_codex_sandbox(cwd: Optional[str]) -> Dict[str, Any]:
    """Sandbox widening for this checkout from the operator's user-level file (#401 SEC-1).

    Never read from the checkout. The file lives outside the repo and is keyed by the
    realpath of the repo top-level: `[repos."/abs/path"]` with `codex_writable_dirs`
    and `codex_network_access`. Needs tomllib (Python 3.11+); a linked worktree also
    matches its main checkout's section.
    """
    out: Dict[str, Any] = {"writable_dirs": [], "network_access": False, "source": None}
    cfg = codex_user_config_path()
    section, top = _user_repo_section(cfg, cwd, "codex sandbox config")
    if section is None:
        return out
    _, common = _checkout_facts(cwd)
    where = f"[repos.\"{top}\"] codex_writable_dirs in {cfg} (#401)"
    dirs = section.get("codex_writable_dirs", [])
    if not isinstance(dirs, list):
        raise ModelPinError(f"{where}: must be a TOML array of paths")
    for raw in dirs:
        d = _check_writable_dir(raw, where, top, common, cfg)
        if d not in out["writable_dirs"]:
            out["writable_dirs"].append(d)
    net = section.get("codex_network_access", False)
    if not isinstance(net, bool):
        raise ModelPinError(f"[repos.\"{top}\"] codex_network_access in {cfg} must be true or false (#401)")
    out["network_access"] = net
    out["source"] = str(cfg)
    return out


def load_codex_writer_settings(cwd: Optional[str] = None) -> Dict[str, Any]:
    """Codex writer timeout (team.toml) and sandbox widening (user-level file only) (#401).

    `codex_timeout_sec` (positive number, at most CODEX_MAX_TIMEOUT_SEC) is read from the
    team.toml discovered from `cwd`. `codex_writable_dirs` / `codex_network_access` are
    NOT read from the checkout, because a PR could widen its own sandbox: a team.toml that
    sets them is ignored (reported in `ignored_repo_keys`) and the operator's
    `codex_user_config_path()` file is the only source. A malformed value fails closed.
    """
    orch, path = _orchestration_table(cwd)
    out: Dict[str, Any] = {
        "timeout": None, "timeout_source": None, "writable_dirs": [], "network_access": False,
        "sandbox_source": None, "ignored_repo_keys": [], "repo_toml": str(path) if path else None,
    }
    if "codex_timeout_sec" in orch:
        try:
            import tomllib  # noqa: F401
        except ImportError:
            raise ModelPinError(
                f"[orchestration] codex_timeout_sec in {path} requires Python 3.11+ (tomllib) "
                f"to be read; upgrade Python or remove the key (#401)") from None
        val = orch["codex_timeout_sec"]
        if (isinstance(val, bool) or not isinstance(val, (int, float))
                or (isinstance(val, float) and not math.isfinite(val)) or val <= 0):
            raise ModelPinError(
                f"[orchestration] codex_timeout_sec in {path} must be a positive number of "
                f"seconds (#401)")
        if val > CODEX_MAX_TIMEOUT_SEC:
            raise ModelPinError(
                f"[orchestration] codex_timeout_sec = {val} in {path} exceeds the cap of "
                f"{int(CODEX_MAX_TIMEOUT_SEC)} s; use --timeout for a one-off longer run (#401)")
        out["timeout"] = float(val)
        out["timeout_source"] = str(path)
    out["ignored_repo_keys"] = [k for k in _CODEX_SANDBOX_KEYS if k in orch]
    user = _load_user_codex_sandbox(cwd)
    out["writable_dirs"] = user["writable_dirs"]
    out["network_access"] = user["network_access"]
    out["sandbox_source"] = user["source"]
    return out


# Writer roles may leave the native harness only for these targets, and only with the
# operator-side opt-in (#409). Codex is the one non-native writer path with a sandbox plus
# worktree isolation; agy and copilot are detected and shown but never offered.
WRITER_ROLES = frozenset({"dev", "sre", "design"})
WRITER_OPT_IN_TARGETS = frozenset({"codex", "native"})


def harnesses_user_config_path() -> Path:
    """Operator-side harness opt-in, `${XDG_CONFIG_HOME:-~/.config}/marc/harnesses.toml` (#409)."""
    return codex_user_config_path().with_name("harnesses.toml")


def load_writer_opt_in(cwd: Optional[str] = None) -> Dict[str, Any]:
    """Operator-recorded target for writer roles in this repo: `writers = "codex" | "native"` (#409).

    Read only from `harnesses_user_config_path()` (never the checkout), with the same
    validation as codex.toml. Returns {"writers": None|"codex"|"native", "source": path|None,
    "repo": realpath of the checkout}. A malformed file or value fails closed to "no opt-in"
    with a stderr warning, so a broken file can never widen routing.
    """
    cfg = harnesses_user_config_path()
    out: Dict[str, Any] = {"writers": None, "source": None, "repo": _main_checkout(*_checkout_facts(cwd))}
    try:
        section, top = _user_repo_section(cfg, cwd, "harness opt-in config")
        if section is None:
            return out
        val = section.get("writers")
        if val is None:
            return out
        if val not in WRITER_OPT_IN_TARGETS:
            raise ModelPinError(
                f"[repos.\"{top}\"] writers in {cfg} must be \"codex\" or \"native\" (#409)")
        out["writers"] = val
        out["source"] = str(cfg)
    except ModelPinError as e:
        sys.stderr.write(f"[mARC dispatch] Warning: ignoring the harness opt-in ({e}); writers stay native.\n")
    return out


def record_block(repo: str, target: str) -> str:
    """The exact TOML block that records the opt-in; the operator shows it before writing (#409)."""
    return f"[repos.{_toml_basic_string(repo)}]\nwriters = {_toml_basic_string(target)}\n"


def _toml_basic_string(text: str) -> str:
    """TOML basic-string literal: escapes backslash, quote and control chars (#409 SEC-3)."""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append("\\u%04X" % ord(ch))
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _binary_hazard(which_path: str, top: str, common: Optional[str]) -> Optional[str]:
    """Why a PATH hit must not be trusted at session start, or None (#409): inside the
    checkout or its git dir, in a world-writable directory, or owned by neither you nor root."""
    try:
        real = os.path.realpath(which_path)
    except OSError:
        return "cannot be inspected"
    if _is_within(real, top) or _is_within(os.path.realpath(os.path.dirname(which_path) or os.sep), top):
        return "resolves inside the checkout"
    if common and _is_within(real, common):
        return "resolves inside the git directory"
    link = _symlink_hazard(which_path)
    if link:
        return f"{link}"
    try:
        me = os.getuid() if hasattr(os, "getuid") else None
        for d in {os.path.dirname(which_path) or os.sep, os.path.dirname(real)}:
            st = os.stat(d)
            if st.st_mode & 0o022:
                return f"sits in a group- or world-writable directory ({d})"
            if me is not None and st.st_uid not in (0, me):
                return f"sits in a directory owned by another user ({d})"
        if me is not None and os.stat(real).st_uid not in (0, me):
            return "is owned by another user"
    except OSError:
        return "cannot be inspected"
    return None


def probe_harnesses(
    which_fn: Callable[[str], Optional[str]] = shutil.which,
    run_fn: Optional[Callable[..., Any]] = None,
    env: Optional[Dict[str, str]] = None,
    cwd: Optional[str] = None,
    probe: bool = False,
    opt_in: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Which harnesses are on this machine and who may route where (#409).

    Executes nothing unless `probe` is set (then `<bin> --version` with a 3 s timeout,
    stdin closed, a temp cwd and no credential-looking env). Never checks auth.
    """
    native = detect_native_harness(env=env, which_fn=which_fn)
    top, common = _checkout_facts(cwd)
    found: List[Dict[str, Any]] = []
    for harness, binary in HARNESS_BINARIES.items():
        entry: Dict[str, Any] = {"harness": harness, "binary": binary, "path": None,
                                 "status": "missing", "native": harness == native,
                                 "auth": "unknown", "version": None}
        hit = which_fn(binary)
        if hit:
            hazard = _binary_hazard(hit, top, common)
            entry["status"] = "untrusted-path" if hazard else "present"
            entry["path"] = hit
            if hazard:
                entry["reason"] = hazard
            elif probe and run_fn is not None:
                entry["version"] = _probe_version(hit, run_fn, env)
        entry["read_only_roles"] = harness == READ_ONLY_PREFERRED_HARNESS
        entry["writer_roles"] = (harness == native
                                 or (harness == "codex" and entry["status"] == "present"))
        found.append(entry)
    opt = opt_in if opt_in is not None else load_writer_opt_in(cwd)
    try:
        team_native = _orchestration_table(cwd)[0].get("mode") == "native"
    except Exception:
        team_native = False
    others = [e for e in found if not e["native"] and e["status"] == "present"]
    codex_ok = any(e["harness"] == "codex" for e in others) and native != "codex"
    return {
        "native": native,
        "harnesses": found,
        "writers": opt.get("writers"),
        "opt_in_source": opt.get("source"),
        "repo": opt.get("repo") or top,
        "ask_needed": codex_ok and opt.get("writers") is None and not team_native,
    }


def _probe_version(path: str, run_fn: Callable[..., Any], env: Optional[Dict[str, str]]) -> Optional[str]:
    clean = {k: v for k, v in (os.environ if env is None else env).items()
             if not re.search(r"KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH", k, re.I)}
    try:
        with tempfile.TemporaryDirectory() as tmp:
            res = run_fn([path, "--version"], capture_output=True, text=True, timeout=3.0,
                         stdin=subprocess.DEVNULL, cwd=tmp, env=clean)
        lines = (getattr(res, "stdout", "") or "").strip().splitlines()
        # First line, version-ish charset only, capped: never raw tool output into the model context (#409 SEC-4).
        clean_line = re.sub(r"[^A-Za-z0-9._+() -]", "", lines[0])[:60].strip() if lines else ""
        return clean_line or None
    except Exception:
        return None


def harnesses_line(report: Dict[str, Any]) -> str:
    """One SessionStart line, or "" when only the native harness is present and nothing is recorded."""
    present = [e for e in report["harnesses"] if e["status"] != "missing"]
    if not [e for e in present if not e["native"]] and report["writers"] is None:
        return ""
    parts = []
    for e in report["harnesses"]:
        tag = ("native" if e["native"] else
               {"present": "on PATH", "untrusted-path": "untrusted-path", "missing": "missing"}[e["status"]])
        parts.append(f"{e['harness']} ({tag})")
    if report["writers"]:
        route = f"writers: {report['writers']} (operator opt-in)"
    elif report["ask_needed"]:
        route = "writers: native (no opt-in; ask once to offer codex)"
    else:
        route = "writers: native"
    return "harnesses: " + " · ".join(parts) + " — " + route


def _main_harnesses(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(prog="dispatch_agent.py harnesses",
                                     description="Detect available harnesses and routing opt-in (#409).")
    parser.add_argument("--json", action="store_true", help="print the full report as JSON")
    parser.add_argument("--line", action="store_true", help="print the one-line summary (empty if single-harness)")
    parser.add_argument("--probe", action="store_true", help="also run `<bin> --version` (3 s, scrubbed env)")
    parser.add_argument("--record", choices=sorted(WRITER_OPT_IN_TARGETS),
                        help="print the exact harnesses.toml block to record (never writes)")
    parser.add_argument("-C", dest="cwd", default=None, help="checkout directory (default: cwd)")
    args = parser.parse_args(argv)
    global _FACTS_CACHE
    _FACTS_CACHE = {}
    try:
        report = probe_harnesses(run_fn=subprocess.run, cwd=args.cwd, probe=args.probe)
    finally:
        _FACTS_CACHE = None
    if args.record:
        sys.stdout.write(f"# append to {harnesses_user_config_path()} (keep it outside the repo, chmod go-w)\n"
                         + record_block(report["repo"], args.record))
    elif args.line:
        line = harnesses_line(report)
        if line:
            print(line)
    else:
        print(json.dumps(report, indent=2))
    return EXIT_OK


def resolve_default_timeout(harness: Optional[str], role: str, cwd: Optional[str] = None) -> float:
    """Timeout when `--timeout` is not given: 300 s, but 1800 s (or team.toml) for Codex writers (#401)."""
    if harness == "codex" and CANONICAL_ROLES[normalize_role(role)] not in READ_ONLY_ROLES:
        configured = load_codex_writer_settings(cwd)["timeout"]
        return configured if configured is not None else CODEX_WRITER_DEFAULT_TIMEOUT
    return DEFAULT_TIMEOUT


def build_codex_readonly_prompt(role: str, mapped_agent: str, body: str, prompt: str) -> str:
    """Persona-first prompt for a Codex read-only reviewer (#351)."""
    canonical = CANONICAL_ROLES[normalize_role(role)]
    parts = [
        f"You are the mARC {mapped_agent} specialist (`@{canonical}`), dispatched through "
        f"`codex exec`, which has no agent selector. Your agent definition follows; it is "
        f"binding. The filesystem is read-only: do not try to edit files, commit or push.",
        f'<agent-definition name="{mapped_agent}">\n{body}\n</agent-definition>',
    ]
    if canonical in CODEX_READONLY_VERDICT_ROLES:
        parts.append(
            "Report contract: follow the Output section above, and make the LAST line of your "
            "final response the verdict line, exactly one of `verdict: BLOCK`, "
            "`verdict: ADVISE`, `verdict: PASS`."
        )
    parts.append(f"Task:\n{prompt}")
    return "\n\n".join(parts)


def build_harness_command(
    harness: str,
    role: str,
    prompt: str,
    codex_opt_in: bool = False,
    cwd: Optional[str] = None,
) -> List[str]:
    """Build CLI execution command for target harness.

    `codex_opt_in` must come from codex_readonly_opt_in() on team.toml routes;
    `cwd` is the checkout a Codex read-only reviewer reads (default: the
    current directory). Both only matter for a read-only role on codex (#351).
    """
    role = normalize_role(role)
    if is_forbidden_readonly_route(role, harness, codex_opt_in):
        # Defense in depth: resolve_route already re-routes read-only roles away
        # from every non-enforcing harness (#323, #330); never build such a command here.
        raise ReadOnlyRoutingError("read-only role requires the verified Claude Code tool boundary (#323, #330)")
    mapped_agent = ROLE_TO_AGENT[role]
    if harness == "codex" and CANONICAL_ROLES[role] in READ_ONLY_ROLES:
        # Opt-in Codex reviewer (#351): fixed argv, never a bypass/full-auto/
        # workspace-write/danger-full-access flag. `-C <checkout>` and not
        # `--worktree`: the reviewer must read the exact checkout the operator
        # put at the PR HEAD, and a managed worktree would be a new write
        # (and a different tree) a read-only role has no reason to make.
        body, pins = load_codex_persona(mapped_agent)
        checkout = str(Path(cwd or os.getcwd()).resolve())
        cmd = ["codex", "exec", "-C", checkout, *CODEX_READONLY_PROFILE_ARGS]
        if "model" in pins:
            cmd += ["-m", pins["model"]]
        if "model_reasoning_effort" in pins:
            cmd += ["-c", f'model_reasoning_effort="{pins["model_reasoning_effort"]}"']
        cmd.append(build_codex_readonly_prompt(role, mapped_agent, body, prompt))
        return cmd
    if harness == "claude-code":
        return ["claude", "--dangerously-skip-permissions", "--agent", mapped_agent, "-p", prompt]
    elif harness == "antigravity":
        # --dangerously-skip-permissions stays unconditional here for now; whether
        # it should become opt-in per call site is a separate decision (#323).
        # The Antigravity plugin ships personas as skills (skills/<name>/SKILL.md),
        # not agents, and agy headless ignores `--agent` anyway (#323). So no
        # `--agent` flag: the prompt tells the stock agent to load the persona skill.
        # Model (#385): team.toml override, else the role's descriptor, else no
        # flag. Only the effort-suffixed slug, never `--effort` too (how the two
        # combine is undocumented). An unknown slug makes headless agy exit
        # non-zero; that failure is returned, never retried without --model.
        model = (load_model_override("antigravity", role, cwd)
                 or load_agy_model(mapped_agent))
        model_args = ["--model", model] if model else []
        return ["agy", "--dangerously-skip-permissions", *model_args, "-p",
                f"Read the '{mapped_agent}' skill instructions before proceeding, "
                f"then act as the mARC {mapped_agent} specialist. {prompt}"]
    elif harness == "copilot":
        return ["copilot", "--prompt", prompt]
    elif harness == "codex":
        # Codex selects configured subagents through the model's Agent tool;
        # `-p` is a profile flag, not an agent selector. State the role in the
        # prompt and use a managed worktree for mutating runs. (Read-only roles
        # returned above, #351.)
        # Pins (#385): a team.toml codex profile for this role wins outright (CLI
        # `-m`/`-c` would override the profile's own model and effort, so none
        # is passed with it); otherwise the persona pins, as on the read-only path.
        profile = load_model_override("codex", role, cwd)
        if profile:
            pin_args = ["-p", profile]
        else:
            pins = load_codex_pins(mapped_agent)
            pin_args = (["-m", pins["model"]] if "model" in pins else []) + (
                ["-c", f'model_reasoning_effort="{pins["model_reasoning_effort"]}"']
                if "model_reasoning_effort" in pins else [])
        # Opt-in sandbox widening (#401), empty by default and supplied only by the
        # operator's user-level file (never the checkout): extra writable dirs for
        # toolchain caches outside the worktree, and network inside the sandbox.
        widen = load_codex_writer_settings(cwd)
        widen_args: List[str] = []
        for d in widen["writable_dirs"]:
            widen_args += ["--add-dir", d]
        if widen["network_access"]:
            widen_args += ["-c", "sandbox_workspace_write.network_access=true"]
        return ["codex", "exec", "--worktree", "--sandbox", "workspace-write", *widen_args,
                *pin_args, f"Act as the mARC {mapped_agent} specialist. {prompt}"]
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
    writer_opt_in: Optional[str] = None,
    repo_path: str = "<absolute repo path>",
    hazard_fn: Optional[Callable[[Any], Optional[str]]] = None,
) -> Dict[str, Any]:
    """Resolve target harness based on priority hierarchy and check CLI availability (origin: #241).

    Priorities:
        1. Explicit --harness flag (if not 'auto' or unset).
        2. User explicit route in team.toml [orchestration.routes]. A route may only
           narrow: a writer role (dev/sre/design) routed off the native harness needs
           the operator-side `writer_opt_in` naming that harness, else it runs native
           with a warning naming the entry to add (#409).
        2b. The operator-side `writer_opt_in` ("codex") for writer roles (#409).
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

    # Codex read-only opt-in (#351): honored only when team.toml routes name
    # codex for the role AND the codex target came from that route or from an
    # explicit `--harness codex`. Native mode/`--harness native`, the matrix,
    # host detection and every fallback never count as an opt-in.
    toml_opt_in = codex_readonly_opt_in(role, toml_routes)
    route_opt_in = False
    writer_reason: Optional[str] = None

    # Priority 1: Explicit --harness argument
    if explicit_harness and explicit_harness != "auto":
        target_harness = host_harness if explicit_harness == "native" else explicit_harness
        route_opt_in = toml_opt_in and explicit_harness == "codex"
    elif toml_mode == "native":
        target_harness = host_harness
    else:
        # Priority 2: User explicit route in team.toml [orchestration.routes]
        user_route = None
        if isinstance(toml_routes, dict):
            user_route = toml_routes.get(canonical_role) or toml_routes.get(role)

        if user_route and user_route != "auto":
            target_harness = host_harness if user_route == "native" else user_route
            route_opt_in = toml_opt_in and user_route == "codex"
            # claude-code is the trusted, sandboxed agent-definition harness: routing to it
            # narrows, so it is honoured on every host (#409 REV-1).
            if (canonical_role in WRITER_ROLES and target_harness != host_harness
                    and target_harness in HARNESS_BINARIES and target_harness != "claude-code"
                    and writer_opt_in != target_harness):
                writer_reason = (f"team.toml routes writer role '{role}' to '{target_harness}' "
                                 f"without the operator-side opt-in (#409)")
                sys.stderr.write(
                    f"[mARC dispatch] Warning: team.toml routes writer role '{role}' to "
                    f"'{target_harness}' but there is no operator-side opt-in, so it runs on "
                    f"'{host_harness}' (#409). To enable it, add to "
                    f"{harnesses_user_config_path()}:\n"
                    + (record_block(repo_path, target_harness) if target_harness == "codex"
                       else f"  (only codex can be opted in; use --harness {target_harness} for a one-off)\n"))
                target_harness = host_harness
                route_opt_in = False
        elif canonical_role in WRITER_ROLES and writer_opt_in == "codex":
            target_harness = "codex"
        else:
            # Priority 3: When route is 'auto', unset, or in hybrid mode, lookup DEFAULT_HYBRID_MATRIX
            matrix = DEFAULT_HYBRID_MATRIX.get(host_harness, DEFAULT_HYBRID_MATRIX.get("claude-code", {}))
            target_harness = matrix.get(canonical_role) or matrix.get(role, host_harness)

    if target_harness == "native":
        target_harness = host_harness

    if target_harness not in HARNESS_BINARIES:
        target_harness = "claude-code"
    if target_harness != "codex":
        route_opt_in = False

    # Fail closed for read-only roles (#323): no source (explicit --harness,
    # team.toml route, native mode, matrix) may land them on a harness that
    # does not apply the agent definition, except the explicit Codex opt-in (#351).
    read_only = canonical_role in READ_ONLY_ROLES
    reroute_reason: Optional[str] = None
    if is_forbidden_readonly_route(role, target_harness, route_opt_in):
        reroute_reason = (
            f"role '{role}' is read-only but harness '{target_harness}' does not apply mARC "
            f"agent definitions in headless dispatch (issue #323)"
        )
        sys.stderr.write(
            f"[mARC dispatch] Warning: {reroute_reason}. "
            f"Re-routing to '{READ_ONLY_PREFERRED_HARNESS}'.\n"
        )
        target_harness = READ_ONLY_PREFERRED_HARNESS
        route_opt_in = False

    hazards: Dict[str, str] = {}

    def _is_available(h_or_b: str) -> bool:
        b = HARNESS_BINARIES.get(h_or_b, h_or_b)
        if cli_checker is None:
            res: Any = shutil.which(b)
        else:
            try:
                res = cli_checker(b)
            except Exception:
                res = None
            if res is None and b != h_or_b:
                try:
                    res = cli_checker(h_or_b)
                except Exception:
                    res = None
        if res and hazard_fn is not None and h_or_b == "codex":
            why = hazard_fn(res)
            if why:
                hazards[h_or_b] = why
                return False
        return bool(res)

    # Check target CLI binary availability
    expected_bin = HARNESS_BINARIES.get(target_harness, target_harness)
    if not _is_available(target_harness):
        fallback_reason = (f"CLI binary '{expected_bin}' for harness '{target_harness}' is untrusted "
                           f"({hazards[target_harness]}); not dispatching to it"
                           if target_harness in hazards else
                           f"CLI binary '{expected_bin}' for harness '{target_harness}' not found on PATH")
        if read_only:
            # Only fall back to a harness that applies agent definitions; never
            # to a NON_ENFORCING_HARNESSES entry, even when it is the host (#323).
            # The predicate runs WITHOUT the opt-in: a fallback never picks codex (#351).
            candidates = [
                h for h in (READ_ONLY_PREFERRED_HARNESS, host_harness)
                if not is_forbidden_readonly_route(role, h)
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
        combined = "; ".join(x for x in (writer_reason, reroute_reason, fallback_reason) if x)
        return {
            "harness": fallback_harness,
            "fallback": True,
            "fallback_reason": combined,
            "policy_reroute": reroute_reason is not None,
            "policy_reason": reroute_reason,
            "cli_fallback": True,
            "codex_readonly_opt_in": False,
        }

    if route_opt_in:
        sys.stderr.write(
            f"[mARC dispatch] Notice: read-only role '{role}' runs on codex by explicit team.toml "
            f"opt-in (#351): filesystem read-only + network profile, persona carried in the prompt.\n"
        )
    else:
        _warn_if_unenforced(target_harness)
    return {
        "harness": target_harness,
        "fallback": reroute_reason is not None or writer_reason is not None,
        "fallback_reason": "; ".join(x for x in (writer_reason, reroute_reason) if x) or None,
        "policy_reroute": reroute_reason is not None,
        "policy_reason": reroute_reason,
        "cli_fallback": False,
        "codex_readonly_opt_in": route_opt_in,
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


def _codex_hazard_fn(cwd: Optional[str]) -> Callable[[Any], Optional[str]]:
    """Same trust check the probe uses, applied to the codex PATH hit at dispatch time (#409 SEC-1)."""
    def check_hit(hit: Any) -> Optional[str]:
        if not isinstance(hit, str) or not os.path.isabs(hit) or not os.path.exists(hit):
            return None
        top, common = _checkout_facts(cwd)
        return _binary_hazard(hit, top, common)
    return check_hit


def resolve_route_detailed(
    role: str,
    requested_harness: str = "auto",
    team_toml_path: Optional[Path] = None,
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
    cwd: Optional[str] = None,
) -> Dict[str, Any]:
    """Like resolve_route, but returns resolve_target_harness_detailed's dict.

    Delegates to resolve_target_harness_detailed with host detection and team.toml config.
    """
    host_harness = detect_native_harness(env=env, which_fn=which_fn)
    config = parse_toml(team_toml_path)
    orchestration = config.get("orchestration", {}) if isinstance(config, dict) else {}
    toml_mode = orchestration.get("mode")
    toml_routes = orchestration.get("routes")

    # Operator-side writer opt-in (#409); read only for writer roles, never from the checkout.
    opt = {"writers": None, "repo": ""}
    if role in CANONICAL_ROLES and CANONICAL_ROLES[role] in WRITER_ROLES:
        opt = load_writer_opt_in(cwd)

    return resolve_target_harness_detailed(
        host_harness=host_harness,
        role=role,
        explicit_harness=requested_harness,
        toml_routes=toml_routes,
        toml_mode=toml_mode,
        cli_checker=which_fn,
        writer_opt_in=opt["writers"],
        repo_path=opt["repo"] or "<absolute repo path>",
        hazard_fn=_codex_hazard_fn(cwd),
    )


def dispatch(
    role: str,
    prompt: str,
    harness: str = "auto",
    timeout: Optional[float] = None,
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
    if timeout is None:
        timeout = result["default_timeout"]
    return execute_prepared(result, timeout=timeout, runner_fn=runner_fn)


def prepare_dispatch(
    role: str,
    prompt: str,
    harness: str = "auto",
    dry_run: bool = False,
    team_toml: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
    which_fn: Callable[[str], Optional[str]] = shutil.which,
    cwd: Optional[Path] = None,
) -> Dict[str, Any]:
    """Resolve the route and build the command, without running it (#352).

    Shared by dispatch() and the `start` subcommand, so routing, the read-only
    fail-closed guard (#323) and CLI fallback are one code path. A returned
    dict that already carries `exit_code` is terminal (refusal or dry run);
    otherwise pass it to execute_prepared().
    """
    # Discover team.toml from the same checkout `-C` and `start` use (#351 REV-3).
    toml_path = find_team_toml(team_toml, start_dir=cwd)
    try:
        role = normalize_role(role)
        route = resolve_route_detailed(
            role=role,
            requested_harness=harness,
            team_toml_path=toml_path,
            env=env,
            which_fn=which_fn,
            cwd=str(cwd) if cwd else None,
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
    opt_in = bool(route.get("codex_readonly_opt_in"))
    try:
        # One rule for sync dispatch, `start` and the runner (#351 REV-7): a
        # Codex read-only opt-in counts only from the team.toml discovered from
        # the checkout. Any other --team-toml (inside or outside it) is refused.
        if opt_in and not _reresolve_codex_opt_in(role, harness, str((cwd or Path.cwd()).resolve())):
            raise ReadOnlyRoutingError(
                f"codex read-only opt-in for '{role}' comes from {toml_path}, not from the team.toml "
                f"discovered from the checkout; refusing (#351)")
        cmd = build_harness_command(resolved_harness, role, prompt,
                                    codex_opt_in=opt_in, cwd=str(cwd) if cwd else None)
        default_timeout = resolve_default_timeout(resolved_harness, role, str(cwd) if cwd else None)
        codex_writer = resolved_harness == "codex" and CANONICAL_ROLES[role] not in READ_ONLY_ROLES
        widen = (load_codex_writer_settings(str(cwd) if cwd else None) if codex_writer
                 else {"writable_dirs": [], "network_access": False, "sandbox_source": None,
                       "timeout_source": None, "ignored_repo_keys": [], "repo_toml": None})
    except (ReadOnlyRoutingError, ModelPinError) as e:
        # e.g. an opted-in Codex reviewer whose persona file is missing (#351),
        # or a malformed model pin / profile (#385).
        err_msg = f"[mARC dispatch] Error: {e}"
        sys.stderr.write(err_msg + "\n")
        return {
            "role": role, "mapped_agent": ROLE_TO_AGENT.get(role), "requested_harness": harness,
            "harness": None, "command": [], "command_str": "", "fallback": False,
            "fallback_reason": None, "policy_reroute": False, "policy_reason": None,
            "dry_run": dry_run, "exit_code": 2, "stdout": "", "stderr": err_msg,
            "success": False, "error": str(e), "duration_sec": 0.0,
        }
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
        "codex_readonly_opt_in": opt_in,
        "team_toml": str(toml_path.resolve()) if toml_path else None,
        "default_timeout": default_timeout,
        "dry_run": dry_run,
    }
    if codex_writer:
        # Make any sandbox widening visible to the dispatcher (#401); never silent.
        result["codex_writable_dirs"] = widen["writable_dirs"]
        result["codex_network_access"] = widen["network_access"]
        result["codex_sandbox_source"] = widen["sandbox_source"]
        result["codex_timeout_source"] = widen["timeout_source"]
        if widen["writable_dirs"] or widen["network_access"]:
            sys.stderr.write(
                f"[mARC dispatch] Notice: codex sandbox widened by {widen['sandbox_source']}: "
                f"writable dirs {widen['writable_dirs']}, "
                f"network_access={widen['network_access']} (#401)\n")
        if widen["ignored_repo_keys"]:
            sys.stderr.write(
                f"[mARC dispatch] Warning: ignoring {', '.join(widen['ignored_repo_keys'])} in "
                f"{widen['repo_toml']}: the checkout cannot widen its own Codex sandbox. Set them "
                f"per repo in {codex_user_config_path()} under [repos.\"<repo top-level>\"] "
                f"instead (#401)\n")
        discovered = widen["repo_toml"]
        if team_toml and toml_path and (
                not discovered or os.path.realpath(str(toml_path)) != os.path.realpath(discovered)):
            other = parse_toml(toml_path).get("orchestration", {})
            if isinstance(other, dict) and any(k in other for k in _CODEX_REPO_KEYS):
                sys.stderr.write(
                    f"[mARC dispatch] Warning: --team-toml {toml_path}: its codex_* keys are "
                    f"ignored; Codex timeout comes from the checkout's team.toml and sandbox "
                    f"widening only from {codex_user_config_path()} (#401)\n")

    if dry_run:
        result.update({
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
            "success": True,
            "duration_sec": 0.0,
        })
    return result


_PARTIAL_MAX_CHARS = 6000


def _git_safe(args: List[str], cwd: Optional[str] = None, timeout: float = 20.0) -> Optional[str]:
    """Read-only git call that cannot run hooks or write the index; None on any failure."""
    env = dict(os.environ, GIT_OPTIONAL_LOCKS="0")
    try:
        r = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _git_worktree_paths() -> Optional[set]:
    out = _git_safe(["worktree", "list", "--porcelain"])
    if out is None:
        return None
    return {line[len("worktree "):].strip() for line in out.splitlines() if line.startswith("worktree ")}


def _clip(text: str) -> Tuple[str, bool]:
    return (text[:_PARTIAL_MAX_CHARS], True) if len(text) > _PARTIAL_MAX_CHARS else (text, False)


def _codex_managed_root() -> str:
    """Realpath of Codex's managed worktree root, `${CODEX_HOME:-~/.codex}/worktrees` (#401 REV-8)."""
    home = os.environ.get("CODEX_HOME") or os.path.join("~", ".codex")
    return os.path.realpath(os.path.join(os.path.expanduser(home), "worktrees"))


def _collect_partial_work(before: Optional[set], started_at: Optional[float] = None) -> Dict[str, Any]:
    """After a Codex timeout: worktrees that appeared during the run, never silently dropped (#401).

    A new worktree whose realpath is under the managed root (`realpath(${CODEX_HOME:-~/.codex})
    /worktrees`, compared by path prefix) AND whose directory was created after the run
    started is the likely one; exactly one such worktree is reported with `attributed:
    "likely"` plus status and diff stat (a concurrent Codex operator could have created it,
    so it is never certain). Every other new worktree is listed in `candidates` (path only,
    `attributed: false`). Nothing is cleaned up or reset; read-only git only.
    """
    after = _git_worktree_paths()
    if after is None or before is None:
        return {"worktrees": [], "candidates": [],
                "note": "could not snapshot git worktrees (before or after the run), so nothing "
                        "can be attributed; look for the Codex managed worktree under "
                        f"{_codex_managed_root()} manually"}
    root = _codex_managed_root()
    managed: List[str] = []
    others: List[str] = []
    for p in sorted(after - before):
        real = os.path.realpath(p)
        fresh = True
        if started_at is not None:
            try:
                fresh = os.stat(real).st_ctime >= started_at - 2.0
            except OSError:
                fresh = False
        (managed if (_is_within(real, root) and real != root and fresh) else others).append(p)
    candidates = [{"path": p, "attributed": False} for p in others]
    if not managed and not candidates:
        return {"worktrees": [], "candidates": [],
                "note": "no new git worktree appeared during the run; Codex may have timed out "
                        "before creating it"}
    if len(managed) != 1:
        candidates = [{"path": p, "attributed": False} for p in managed + others]
        return {"worktrees": [], "candidates": candidates,
                "note": "no single new Codex-managed worktree can be attributed to this dispatch "
                        "(none, or several, appeared during the run; another operator may have "
                        "created some); inspect them yourself before touching any"}
    path = managed[0]
    entry: Dict[str, Any] = {"path": path, "attributed": "likely"}
    status = _git_safe(["status", "--short", "--untracked-files=all"], cwd=path)
    stat_out = _git_safe(["diff", "--stat", "HEAD"], cwd=path)
    if status is None or stat_out is None:
        entry.update({"git_status": "", "diff_stat": "", "has_changes": None, "truncated": False,
                      "error": "git status/diff failed in this worktree; its state is unknown"})
    else:
        status, st_cut = _clip(status)
        stat_out, ds_cut = _clip(stat_out)
        entry.update({"git_status": status, "diff_stat": stat_out,
                      "has_changes": bool(status.strip()), "truncated": st_cut or ds_cut})
    return {"worktrees": [entry], "candidates": candidates,
            "note": "the only new Codex-managed worktree during the run, so probably this "
                    "dispatch's (a concurrent Codex operator could own it); kept as is. Inspect "
                    "it, then finish, adopt or remove it yourself"}


def _reap_after_kill(proc: "subprocess.Popen[str]", cmd: List[str], timeout: Optional[float],
                     grace: float = 2.0) -> "subprocess.TimeoutExpired":
    """Collect output after the group was killed, bounded (#401 REV-7).

    A descendant that left the group (setsid) may still hold the pipes, which would make an
    unbounded communicate() hang; after `grace` seconds the pipes are closed instead.
    """
    out: Any = ""
    err: Any = ""
    try:
        out, err = proc.communicate(timeout=grace)
    except subprocess.TimeoutExpired as e:
        out, err = e.stdout or "", e.stderr or ""
        for pipe in (proc.stdout, proc.stderr):
            try:
                if pipe:
                    pipe.close()
            except Exception:
                pass
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            pass
    return subprocess.TimeoutExpired(cmd, timeout, output=out, stderr=err)


def _killpg_quiet(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except OSError:
        pass


def _run_sync_in_group(cmd: List[str], capture_output: bool = True, text: bool = True,
                       timeout: Optional[float] = None) -> "subprocess.CompletedProcess[str]":
    """subprocess.run()-compatible runner for sync Codex writers (#401 SEC-4).

    Like the detached runner: the harness gets its own process group, which is killed on
    timeout, on interrupt, on SIGTERM/SIGHUP to this process, and (Linux) if this process
    dies by SIGKILL. stdin is /dev/null, as in the runner.
    """
    global _PRCTL
    if _PRCTL is None and sys.platform.startswith("linux"):
        try:
            import ctypes
            _PRCTL = ctypes.CDLL(None, use_errno=True).prctl
        except Exception:
            _PRCTL = None
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, start_new_session=True,
                            preexec_fn=_set_pdeathsig if _PRCTL is not None else None)

    def _on_term(signum: int, _frame: Any) -> None:
        _killpg_quiet(proc.pid, signal.SIGKILL)
        raise SystemExit(128 + signum)

    saved: Dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        for sig in (signal.SIGTERM, signal.SIGHUP):
            saved[sig] = signal.signal(sig, _on_term)
    try:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _killpg_quiet(proc.pid, signal.SIGKILL)
            raise _reap_after_kill(proc, cmd, timeout)
        except BaseException:
            _killpg_quiet(proc.pid, signal.SIGKILL)
            proc.wait()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        _killpg_quiet(proc.pid, signal.SIGTERM)
        for sig, old in saved.items():
            signal.signal(sig, old)


def execute_prepared(
    result: Dict[str, Any],
    timeout: Optional[float] = None,
    runner_fn: Callable[..., Any] = subprocess.run,
) -> Dict[str, Any]:
    """Run a command prepared by prepare_dispatch() and record its outcome."""
    result = dict(result)
    cmd = result["command"]
    if timeout is None:
        timeout = float(result.get("default_timeout", DEFAULT_TIMEOUT))
    result["timeout_sec"] = timeout
    # Codex writer runs work in a managed worktree; remember which ones exist so a
    # timeout can point at the new one and its partial diff (#401).
    codex_writer = (result.get("harness") == "codex"
                    and not result.get("codex_readonly_opt_in")
                    and CANONICAL_ROLES.get(str(result.get("role")), "") not in READ_ONLY_ROLES)
    worktrees_before = _git_worktree_paths() if codex_writer else None
    # The group-killing runner needs POSIX process groups; elsewhere keep plain subprocess.run.
    if (codex_writer and runner_fn is subprocess.run
            and hasattr(os, "killpg") and hasattr(signal, "SIGHUP")):
        runner_fn = _run_sync_in_group
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
            "timed_out": True,
            "duration_sec": round(duration, 3),
        })
        if codex_writer:
            result["partial_work"] = _collect_partial_work(worktrees_before, start_time)
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
# `wait --store <dir>` overrides the lookup for callers outside the clone.
#
# Liveness is a lock, not a pid: the runner holds an exclusive flock on
# `<job_dir>/lock` for its whole lifetime. `start` takes that lock before the
# job dir becomes visible and hands the open descriptor to the runner, so a
# live job is never seen unlocked. `wait` probes with LOCK_SH|LOCK_NB, which
# works across PID namespaces (a sandboxed `wait` cannot see the runner's pid)
# and on a read-only mount. Held -> running; free with a result -> finished;
# free without a result -> lost.
# ---------------------------------------------------------------------------

# wait exit codes. 75 is EX_TEMPFAIL from sysexits.h: "try again later".
EXIT_OK = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_STILL_RUNNING = 75

_JOB_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-")
DEFAULT_WAIT_TIMEOUT = 25.0
LOCK_NAME = "lock"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


class JobStoreError(RuntimeError):
    """The job store cannot be created, read or used on this platform (#352)."""


class _RunnerTerminated(Exception):
    """The runner received a termination signal while the harness was running."""


def _require_flock() -> None:
    if fcntl is None:
        raise JobStoreError("start/wait need POSIX flock, which this platform does not provide")


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
    return Path(state).resolve() / "marc" / "jobs"


def _valid_job_id(job_id: str) -> bool:
    return bool(job_id) and len(job_id) <= 64 and set(job_id) <= _JOB_ID_CHARS


def _new_job_id() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime()) + "-" + os.urandom(4).hex()


def _write_json_atomic(path: Path, data: Dict[str, Any]) -> None:
    """Write JSON via a fresh temp file + fsync + rename; readers never see a partial file."""
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{os.urandom(4).hex()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o600)
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


def _lock_state(job_dir: Path) -> str:
    """'held' while the runner lives, 'free' once it is gone, 'missing' if never created,
    'invalid' if `lock` is not a regular file (e.g. a FIFO or symlink in a foreign store).

    Opened non-blocking so a FIFO planted at `lock` cannot hang `wait` (#352 SEC-7).
    """
    try:
        fd = os.open(str(job_dir / LOCK_NAME), os.O_RDONLY | os.O_NONBLOCK | _O_NOFOLLOW)
    except FileNotFoundError:
        return "missing"
    except OSError:
        return "invalid"
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return "invalid"
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except (BlockingIOError, PermissionError):
            return "held"
        fcntl.flock(fd, fcntl.LOCK_UN)
        return "free"
    finally:
        os.close(fd)


def _status_of_result(result: Dict[str, Any]) -> str:
    if result.get("success"):
        return "succeeded"
    if result.get("timed_out") is True:
        return "timed_out"
    return "failed"


def start_job(
    role: str,
    prompt: str,
    harness: str = "auto",
    timeout: Optional[float] = None,
    team_toml: Optional[str] = None,
    cwd: Optional[Path] = None,
) -> Tuple[int, Dict[str, Any]]:
    """Resolve the route and launch a detached runner. Returns (exit_code, summary).

    Raises JobStoreError when the job store cannot be created (e.g. a read-only
    git dir inside a sandbox).
    """
    _require_flock()
    workdir = (cwd or Path.cwd()).resolve()
    prepared = prepare_dispatch(role=role, prompt=prompt, harness=harness, team_toml=team_toml,
                                cwd=workdir)
    if "exit_code" in prepared:
        # Fail closed (#323) or unknown role: no job is created, nothing runs.
        return prepared["exit_code"], {"error": prepared.get("error"), "exit_code": prepared["exit_code"]}
    try:
        # Refuse now, not in the runner, what the runner's re-check would refuse
        # (e.g. a Codex read-only opt-in from any --team-toml other than the one
        # discovered from the checkout, #351).
        _validate_spec({"prompt": prompt, "cwd": str(workdir), "prepared": prepared})
    except ReadOnlyRoutingError as e:
        sys.stderr.write(f"[mARC dispatch] Error: {e}\n")
        return EXIT_USAGE, {"error": str(e), "exit_code": EXIT_USAGE}

    if timeout is None:
        timeout = prepared["default_timeout"]
    root = jobs_root(workdir)
    job_id = _new_job_id()
    job_dir = root / job_id
    result_path = job_dir / "result.json"
    lock_fd = -1
    spawned = False
    stage = "cannot create job under"
    try:
        root.parent.parent.mkdir(parents=True, exist_ok=True)
        for d in (root.parent, root):
            d.mkdir(mode=0o700, exist_ok=True)
        # Build the job in a staging dir (invisible to wait), lock it, then
        # publish it with one rename: wait never sees a half-created job.
        staging = root / f".tmp-{job_id}"
        staging.mkdir(mode=0o700)
        lock_fd = os.open(str(staging / LOCK_NAME), os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o600)
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        started_at = time.time()
        _write_json_atomic(staging / "spec.json", {
            "job_id": job_id,
            "timeout": timeout,
            "cwd": str(workdir),
            "prompt": prompt,
            "prepared": prepared,
        })
        meta = {
            "job_id": job_id,
            "pid": None,
            "role": prepared["role"],
            "harness": prepared["harness"],
            "result_path": str(result_path),
            "started_at": started_at,
            "timeout": timeout,
        }
        _write_json_atomic(staging / "job.json", meta)
        os.rename(str(staging), str(job_dir))
        stage = "failed to spawn runner under"
        log_fd = os.open(str(job_dir / "runner.log"), os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_NOFOLLOW, 0o600)
        try:
            proc = subprocess.Popen(
                [sys.executable, os.path.abspath(__file__), "_run", str(job_dir), "--lock-fd", str(lock_fd)],
                cwd=str(workdir),
                stdin=subprocess.DEVNULL,
                stdout=log_fd,
                stderr=log_fd,
                pass_fds=(lock_fd,),
                start_new_session=True,
            )
        finally:
            os.close(log_fd)
        spawned = True
        meta["pid"] = proc.pid
        try:
            _write_json_atomic(job_dir / "job.json", meta)
        except OSError:
            pass  # informational only; liveness comes from the lock
    except BaseException as e:
        if not spawned:
            # Nothing runs: remove the staging dir or the already-published
            # job, whatever the failure, so neither lingers (#352 review).
            for leftover in (root / f".tmp-{job_id}", job_dir):
                shutil.rmtree(str(leftover), ignore_errors=True)
        if isinstance(e, OSError):
            raise JobStoreError(f"{stage} {root}: {e.strerror or e}") from e
        raise
    finally:
        if lock_fd >= 0:
            # The runner now holds the lock through its inherited descriptor.
            os.close(lock_fd)
    summary = {
        "job_id": job_id,
        "pid": proc.pid,
        "role": prepared["role"],
        "harness": prepared["harness"],
        "result_path": str(result_path),
    }
    return EXIT_OK, summary


# Process group of the running harness, killed on runner exit/termination (#352).
_HARNESS_PGID: Optional[int] = None
_PRCTL: Any = None


def _kill_harness_group(sig: int = signal.SIGTERM) -> None:
    pgid = _HARNESS_PGID
    if pgid:
        try:
            os.killpg(pgid, sig)
        except OSError:
            pass


def _set_pdeathsig() -> None:
    """In the harness child (Linux): get SIGTERM if the runner dies, even by SIGKILL."""
    if _PRCTL is not None:
        try:
            _PRCTL(1, int(signal.SIGTERM))  # PR_SET_PDEATHSIG
        except Exception:
            pass


def _run_in_own_group(cmd: List[str], capture_output: bool = True, text: bool = True,
                      timeout: Optional[float] = None) -> "subprocess.CompletedProcess[str]":
    """subprocess.run()-compatible runner that puts the harness in its own process group."""
    global _HARNESS_PGID
    proc = subprocess.Popen(
        cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, start_new_session=True,
        preexec_fn=_set_pdeathsig if _PRCTL is not None else None,
    )
    _HARNESS_PGID = proc.pid
    try:
        try:
            if _TERM_PENDING is not None:
                # Signalled before the harness started: don't let it run.
                raise _RunnerTerminated(f"runner terminated by signal {_TERM_PENDING}")
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_harness_group(signal.SIGKILL)
            raise _reap_after_kill(proc, cmd, timeout)
        except BaseException:
            _kill_harness_group(signal.SIGKILL)
            proc.wait()
            raise
        return subprocess.CompletedProcess(cmd, proc.returncode, out, err)
    finally:
        # Leftover background processes of the harness go with it.
        _kill_harness_group(signal.SIGTERM)
        _HARNESS_PGID = None


_TERM_PENDING: Optional[int] = None


def _on_term_signal(signum: int, _frame: Any) -> None:
    """Interrupt only a running harness; otherwise just record the signal.

    Raising outside the harness run (e.g. during the final result.json write)
    would leave the job without a result, so the signal is deferred there and
    the runner still writes its terminal result before exiting (#352 review).
    """
    global _TERM_PENDING
    _TERM_PENDING = signum
    if _HARNESS_PGID is not None:
        raise _RunnerTerminated(f"runner terminated by signal {signum}")


def _reresolve_codex_opt_in(role: str, requested: Any, cwd: Optional[str]) -> bool:
    """Re-derive the Codex read-only opt-in for a job spec (#351 REV-2).

    Nothing in the spec is trusted but the checkout dir: team.toml is
    re-discovered from it (never the spec's recorded path, so `start
    --team-toml <elsewhere>` cannot opt in), and the full orchestration config
    (`mode` included) goes back through resolve_target_harness_detailed. The
    requested harness only matters when it is exactly "codex" (route plus
    explicit flag); anything else re-resolves as "auto".
    """
    if not cwd or not Path(cwd).is_dir():
        return False
    config = parse_toml(find_team_toml(None, start_dir=Path(cwd)))
    orch = config.get("orchestration", {}) if isinstance(config, dict) else {}
    if not isinstance(orch, dict):
        return False
    old_stderr = sys.stderr
    sys.stderr = open(os.devnull, "w")
    try:
        d = resolve_target_harness_detailed(
            host_harness=READ_ONLY_PREFERRED_HARNESS, role=role,
            explicit_harness="codex" if requested == "codex" else "auto",
            toml_routes=orch.get("routes"), toml_mode=orch.get("mode"),
            cli_checker=lambda _b: True,
        )
    except (ReadOnlyRoutingError, UnknownRoleError):
        return False
    finally:
        sys.stderr.close()
        sys.stderr = old_stderr
    return d["harness"] == "codex" and bool(d.get("codex_readonly_opt_in"))


def _validate_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    """Re-check a job spec before exec (#352 SEC-1): the routed command, #323 guard included."""
    prepared = spec.get("prepared")
    prompt = spec.get("prompt")
    if not isinstance(prepared, dict) or not isinstance(prompt, str):
        raise RuntimeError("job spec missing or unreadable")
    role = normalize_role(str(prepared.get("role", "")))
    harness = prepared.get("harness")
    cwd = spec.get("cwd")
    cwd = cwd if isinstance(cwd, str) and cwd else None
    opt_in = False
    if harness == "codex" and CANONICAL_ROLES[role] in READ_ONLY_ROLES:
        opt_in = _reresolve_codex_opt_in(role, prepared.get("requested_harness"), cwd)
    if is_forbidden_readonly_route(role, harness, opt_in):
        raise ReadOnlyRoutingError(
            f"job spec routes read-only role '{role}' to '{harness}'; refusing (issue #323)")
    expected = build_harness_command(str(harness), role, prompt, codex_opt_in=opt_in, cwd=cwd)
    if prepared.get("command") != expected:
        raise RuntimeError("job spec command does not match the routed harness command")
    return prepared


def run_job(job_dir_arg: str, lock_fd: Optional[int] = None) -> int:
    """Detached runner body: validate, execute, write result.json atomically, then release the lock."""
    global _PRCTL
    try:
        _require_flock()
        job_dir = Path(job_dir_arg).resolve()
        root = jobs_root()
        if job_dir.parent != root or not _valid_job_id(job_dir.name):
            raise JobStoreError(f"job dir {job_dir_arg!r} is not inside the job store {root}")
        if lock_fd is None:
            lock_fd = os.open(str(job_dir / LOCK_NAME), os.O_RDWR | _O_NOFOLLOW)
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise JobStoreError(f"job {job_dir.name} already has a live runner")
        if (job_dir / "result.json").exists():
            raise JobStoreError(f"job {job_dir.name} already finished")
    except (OSError, JobStoreError) as e:
        sys.stderr.write(f"[mARC dispatch] Error: {e}\n")
        return EXIT_USAGE
    # lock_fd stays open (and locked) until this process exits.

    if sys.platform.startswith("linux"):
        try:
            import ctypes
            _PRCTL = ctypes.CDLL(None, use_errno=True).prctl
        except Exception:
            _PRCTL = None
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, _on_term_signal)
    atexit.register(_kill_harness_group)

    spec = _read_json(job_dir / "spec.json") or {}
    prepared: Any = spec.get("prepared")
    try:
        prepared = _validate_spec(spec)
        result = execute_prepared(prepared, timeout=float(spec.get("timeout", 300.0)),
                                  runner_fn=_run_in_own_group)
    except Exception as e:  # never leave a job without a result
        base = prepared if isinstance(prepared, dict) else {}
        result = dict(base, exit_code=2 if isinstance(e, (ReadOnlyRoutingError, ModelPinError)) else 1,
                      stdout="", stderr=f"[mARC dispatch] Error: {e}",
                      success=False, error=str(e), duration_sec=0.0)
    result["job_id"] = spec.get("job_id", job_dir.name)
    result["status"] = _status_of_result(result)
    _write_json_atomic(job_dir / "result.json", result)
    return EXIT_OK


def job_summary(job_id: str, root: Path) -> Dict[str, Any]:
    """Compact status for one job: never includes command/stdout (#352)."""
    job_dir = root / job_id
    result_path = job_dir / "result.json"
    result = _read_json(result_path)
    if result is None:
        if _lock_state(job_dir) == "held":
            meta = _read_json(job_dir / "job.json") or {}
            started = meta.get("started_at")
            elapsed = round(time.time() - started, 3) if isinstance(started, (int, float)) else None
            return {"job_id": job_id, "status": "running", "exit_code": None,
                    "duration_sec": elapsed, "result_path": str(result_path)}
        # The runner writes its result before releasing the lock; re-read
        # in case it finished between the two checks.
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
    was lost (other jobs may still be running: check the status lines); 75
    timeout reached with job(s) still running and none failed.
    """
    _require_flock()
    root = root or jobs_root()
    deadline = time.monotonic() + max(0.0, timeout)
    interval = 0.1
    finished: Dict[str, Dict[str, Any]] = {}
    while True:
        # Finished jobs are terminal: parse their result.json once, not every poll.
        summaries = [finished[j] if j in finished else job_summary(j, root) for j in job_ids]
        finished.update({s["job_id"]: s for s in summaries if s["status"] != "running"})
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
    group.add_argument("--prompt-file", help="Read the prompt from a file (keeps it out of the process list)")
    parser.add_argument("--harness", default="auto",
                        choices=["auto", "native", "claude-code", "antigravity", "copilot", "codex"])
    parser.add_argument("--timeout", type=float, default=None,
                        help="Harness execution timeout in seconds. Default: 300; 1800 for Codex "
                             "writers (team.toml [orchestration] codex_timeout_sec)")
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
    try:
        code, summary = start_job(args.role, prompt, args.harness, args.timeout, args.team_toml)
    except JobStoreError as e:
        sys.stderr.write(f"[mARC dispatch] Error: {e}\n")
        return EXIT_USAGE
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
    parser.add_argument("--store", default=None,
                        help="Job store dir (the parent of the job dir in start's result_path). "
                             "Default: <git-common-dir>/marc/jobs of the current directory")
    args = parser.parse_args(argv)
    try:
        _require_flock()
        root = Path(args.store).expanduser().resolve() if args.store else jobs_root()
        for j in args.job_ids:
            if not _valid_job_id(j) or not (root / j).is_dir():
                sys.stderr.write(f"[mARC dispatch] Error: unknown job id {j!r} under {root}\n")
                return EXIT_USAGE
        code, summaries = wait_jobs(args.job_ids, args.timeout, args.any, root)
    except (OSError, JobStoreError) as e:
        sys.stderr.write(f"[mARC dispatch] Error: cannot read job store: {e}\n")
        return EXIT_USAGE
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
        help="Specialist role (dev, sre, design, sec, rev, research, arch, engineer, security, review, architect)",
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
        default=None,
        help="Execution timeout in seconds. Default: 300; 1800 for Codex writers "
             "(team.toml [orchestration] codex_timeout_sec)",
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
    if argv and argv[0] == "harnesses":
        return _main_harnesses(argv[1:])
    if argv and argv[0] in ("task", "task-usage"):
        # Codex implements -> suite -> PR -> headless @rev/@sec -> fix rounds (#361).
        import task_pipeline
        return (task_pipeline.main if argv[0] == "task" else task_pipeline.usage_main)(argv[1:])
    if argv and argv[0] == "_run":
        if len(argv) == 2:
            return run_job(argv[1])
        if len(argv) == 4 and argv[2] == "--lock-fd" and argv[3].isdigit():
            return run_job(argv[1], int(argv[3]))
        sys.stderr.write("[mARC dispatch] Error: usage: _run <job_dir> [--lock-fd N]\n")
        return EXIT_USAGE
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
