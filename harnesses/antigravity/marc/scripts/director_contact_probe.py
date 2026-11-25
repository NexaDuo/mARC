#!/usr/bin/env python3
"""Read-only first-use discovery for the engineering director (issue #433).

Reports which harness CLIs are on PATH (with `--version`), which terminal
multiplexer is in use (environment markers only), whether Claude Code
cross-session messaging is available (CLI >= 2.1.224), and proposes an ordered
contact-adapter list per harness plus an optional per-team binding.

Read-only by construction: it writes no file, opens no socket or network
connection, and runs no command except `<cli> --version` for the four harness
CLIs (timeout 10s, no stdin, captured output). It never calls a multiplexer and
never sends a message. Pane-id environment values are reported as present/absent
only. It always exits 0, except 2 for an unknown harness in `--team`.
Persisting a proposal is a separate, opt-in step done by the director.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys

SCHEMA = 1
CROSS_SESSION_MIN = (2, 1, 224)

# harness name -> CLI binary
HARNESSES = {
    "claude-code": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "antigravity": "agy",
}

# multiplexer name -> (activity env marker, binary, pane-id env)
MULTIPLEXERS = {
    "tmux": ("TMUX", "tmux", "TMUX_PANE"),
    "zellij": ("ZELLIJ", "zellij", "ZELLIJ_PANE_ID"),
    "wezterm": ("WEZTERM_PANE", "wezterm", "WEZTERM_PANE"),
}

ADAPTER_ORDER = {
    "claude-code": ["send-message", "resume", "inbox"],
    "codex": ["exec-resume", "inbox"],
    "copilot": ["resume", "inbox"],
    "antigravity": ["conversation-resume", "inbox"],
}

ADAPTER_HELP = {
    "send-message": "SendMessage to a session named via --name (cross-session messaging; needs claude >= 2.1.224)",
    "resume": "claude --resume <name|id>  |  copilot --resume <id>  (only after verified exit)",
    "exec-resume": 'codex exec resume <id> "<prompt>"  (only after verified exit)',
    "conversation-resume": "agy --conversation <id>  (only after verified exit)",
    "inbox": "file or tracker-comment inbox; durable record, never wakes an idle session",
    "multiplexer": "send to an explicit, verified pane id; only after native and resume paths",
}


def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def cli_version(path):
    try:
        r = subprocess.run(
            [path, "--version"], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=10, check=False,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def host_harness(env):
    if env.get("CODEX_PROJECT_DIR") or env.get("CODEX_THREAD_ID"):
        return "codex"
    if env.get("CLAUDECODE") or env.get("CLAUDE_CODE_ENTRYPOINT"):
        return "claude-code"
    return None


def detect_multiplexer(env):
    active = None
    available = []
    pane = False
    for name, (marker, binary, pane_env) in MULTIPLEXERS.items():
        if shutil.which(binary):
            available.append(name)
        if active is None and env.get(marker):
            active = name
            pane = bool(env.get(pane_env))
    return active, available, pane


def build(teams):
    env = os.environ
    harnesses = {}
    for name, binary in HARNESSES.items():
        path = shutil.which(binary)
        harnesses[name] = {"on_path": bool(path), "version": cli_version(path) if path else None}
    active, available, pane = detect_multiplexer(env)
    parsed = parse_version(harnesses["claude-code"]["version"])
    cross = None if parsed is None else parsed >= CROSS_SESSION_MIN
    use_mux = bool(active or available)
    adapters = {}
    for name, order in ADAPTER_ORDER.items():
        ids = [a for a in order if not (a == "send-message" and cross is False)]
        if use_mux:
            ids.append("multiplexer")
        adapters[name] = ids
    return {
        "schema": SCHEMA,
        "host_harness": host_harness(env),
        "harnesses": harnesses,
        "multiplexer": {"active": active, "available": available, "pane_id_env_present": pane},
        "claude_cross_session": cross,
        "adapters": adapters,
        "adapter_help": ADAPTER_HELP,
        "teams": [{"name": n, "harness": h, "adapters": adapters[h]} for n, h in teams],
        "persist": {
            "written": False,
            "note": "proposal only; persist via opt-in after showing content and destination",
        },
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="Read-only contact-adapter discovery (#433).")
    ap.add_argument("--team", action="append", default=[], metavar="NAME=HARNESS",
                    help="propose a binding for a team (repeatable)")
    ap.add_argument("--json", action="store_true", help="JSON output (default)")
    args = ap.parse_args(argv)
    teams = []
    for spec in args.team:
        name, sep, harness = spec.partition("=")
        if not sep or not name or harness not in HARNESSES:
            print(f"error: --team expects NAME=HARNESS with HARNESS in {sorted(HARNESSES)}: {spec!r}",
                  file=sys.stderr)
            return 2
        teams.append((name, harness))
    print(json.dumps(build(teams), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
