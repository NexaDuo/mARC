#!/usr/bin/env python3
"""Read-only contact discovery for the engineering director (issue #433).

Run on every resume and before contacting a team; stored bindings are caches.

Reports which harness CLIs are on PATH (with `--version`), which terminal
multiplexer is in use (environment markers; binaries on PATH or ~/.local/bin), whether Claude Code
cross-session messaging is available (CLI >= 2.1.224), and proposes an ordered
contact-adapter list per harness plus an optional per-team binding.

Read-only by construction: it writes no file, opens no socket or network
connection, and runs no command except `<cli> --version` for the four harness
CLIs and, for a codex on PATH, `codex app-server --help` and `codex --help` (timeout 10s, no stdin, captured output; override with the env var
MARC_PROBE_TIMEOUT, a finite number in (0, 60] seconds, for tests). Only with the opt-in `--list-sessions`
it also runs the read-only `tmux ls` / `zellij list-sessions` (timeout 5s, no
stdin). It never sends a message. Pane-id environment values are reported as present/absent
only. It always exits 0, except 2 for an unknown harness in `--team`.
A binary found only in ~/.local/bin (not on PATH) is reported as `on_path: false,
found_in_user_bin: true` and is never executed: no `--version`, no session listing.
`send-message` is dropped when claude is not on PATH or is older than 2.1.224; when
claude is on PATH but its version is unknown or unparsable (`claude_cross_session:
null`) it is kept as UNVERIFIED and the director must confirm with `ListAgents`.
Persisting a proposal is a separate, opt-in step done by the director.
"""
import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys

SCHEMA = 3
CROSS_SESSION_MIN = (2, 1, 224)

# harness name -> CLI binary
HARNESSES = {
    "claude-code": "claude",
    "codex": "codex",
    "copilot": "copilot",
    "antigravity": "agy",
}

# multiplexer name -> (activity env markers, binary, pane-id env)
MULTIPLEXERS = {
    "tmux": (("TMUX",), "tmux", "TMUX_PANE"),
    "zellij": (("ZELLIJ", "ZELLIJ_SESSION_NAME"), "zellij", "ZELLIJ_PANE_ID"),
    "screen": (("STY",), "screen", "WINDOW"),
    "wezterm": (("WEZTERM_PANE",), "wezterm", "WEZTERM_PANE"),
    "kitty": (("KITTY_WINDOW_ID",), "kitty", "KITTY_WINDOW_ID"),
}

LIST_SESSIONS_CMDS = {
    "tmux": ["tmux", "ls", "-F", "#{session_name}"],
    "zellij": ["zellij", "list-sessions", "--short", "--no-formatting"],
}
# exit-1 messages that mean "none running", not a failure (tmux, zellij)
# "error connecting to" counts only with "no such file or directory" (a missing
# socket); other causes such as "Permission denied" are real failures.
NO_SESSIONS = ("no server running", "no active zellij sessions")


def none_running(msg):
    msg = msg.lower()
    return (any(m in msg for m in NO_SESSIONS)
            or ("error connecting to" in msg and "no such file or directory" in msg))
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")

ADAPTER_ORDER = {
    "claude-code": ["send-message", "multiplexer-interactive", "resume", "inbox"],
    "codex": ["multiplexer-interactive", "exec-resume", "inbox"],
    "copilot": ["multiplexer-interactive", "resume", "inbox"],
    "antigravity": ["multiplexer-interactive", "conversation-resume", "inbox"],
}

ADAPTER_HELP = {
    "app-server-steer": "techlead must run `codex --remote <unix socket or ws with --ws-auth>`; director: thread/loaded/list -> verify thread -> turn/steer (running, expectedTurnId) or turn/start (idle); experimental",
    "send-message": "ListAgents/SendMessage to a session addressed by display name (cross-session messaging; needs claude >= 2.1.224; if claude_cross_session is null the version is unknown, so unverified: confirm with ListAgents)",
    "multiplexer-interactive": "start the harness interactively in a detected multiplexer pane (claude: add --name <team>); type-in only to an explicit, verified pane id",
    "resume": "claude --resume <name|id>  |  copilot --resume <id>  (only after verified exit)",
    "exec-resume": 'codex exec resume <id> "<prompt>"  (only after verified exit)',
    "conversation-resume": "agy --conversation <id>  (only after verified exit)",
    "inbox": "file or tracker-comment inbox; durable record, never wakes an idle session",
}


def probe_timeout():
    try:
        t = float(os.environ.get("MARC_PROBE_TIMEOUT", "10"))
    except ValueError:
        return 10.0
    return t if math.isfinite(t) and 0 < t <= 60 else 10.0


def find_binary(name):
    """PATH only: the sole kind of binary this probe ever executes."""
    return shutil.which(name)


def _local_bin(name):
    p = os.path.join(os.path.expanduser("~/.local/bin"), name)
    return p if os.path.isfile(p) and os.access(p, os.X_OK) else None


def parse_version(text):
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", text or "")
    return tuple(int(x) for x in m.groups()) if m else None


def cli_version(path):
    try:
        r = subprocess.run(
            [path, "--version"], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=probe_timeout(), check=False,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or r.stderr or "").strip().splitlines()
    return out[0].strip() if out else None


def cli_help(path, *args):
    """Help text of `<path> <args> --help`, or None on any failure. Read-only."""
    try:
        r = subprocess.run(
            [path, *args, "--help"], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=probe_timeout(), check=False,
        )
    except Exception:
        return None
    return (r.stdout + r.stderr) if r.returncode == 0 else None


def codex_app_server(path):
    """True/False from help text only (never starts or connects); None on failure."""
    server = cli_help(path, "app-server")
    top = cli_help(path)
    if server is None or top is None:
        return None
    return "--listen" in server and "--remote" in top


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
    for name, (markers, binary, pane_env) in MULTIPLEXERS.items():
        if find_binary(binary) or _local_bin(binary):
            available.append(name)
        if active is None and any(env.get(m) for m in markers):
            active = name
            pane = bool(env.get(pane_env))
    return active, available, pane


def list_sessions(available):
    out = {}
    for name, cmd in LIST_SESSIONS_CMDS.items():
        if name not in available:
            continue
        path = find_binary(name)
        if not path:  # user-bin-only binaries are never executed
            continue
        cmd = [path] + cmd[1:]
        try:
            r = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True,
                               text=True, timeout=5, check=False)
        except Exception as e:
            out[name] = {"sessions": [], "error": type(e).__name__}
            continue
        if r.returncode != 0:
            msg = r.stderr + r.stdout
            if none_running(msg):
                out[name] = {"sessions": []}
                continue
            out[name] = {"sessions": [], "error": f"exit {r.returncode}"}
            continue
        lines = [ANSI.sub("", ln).strip() for ln in r.stdout.splitlines()]
        out[name] = {"sessions": [ln for ln in lines if ln]}
    return out


def build(teams, with_sessions=False):
    env = os.environ
    harnesses = {}
    for name, binary in HARNESSES.items():
        path = find_binary(binary)
        harnesses[name] = {"on_path": bool(path), "version": cli_version(path) if path else None}
        if not path and _local_bin(binary):
            harnesses[name]["found_in_user_bin"] = True
    codex = harnesses["codex"]
    codex["app_server"] = codex_app_server(find_binary("codex")) if codex["on_path"] else None
    active, available, pane = detect_multiplexer(env)
    parsed = parse_version(harnesses["claude-code"]["version"])
    cross = None if parsed is None else parsed >= CROSS_SESSION_MIN
    use_mux = bool(active or available)
    adapters = {}
    for name, order in ADAPTER_ORDER.items():
        adapters[name] = [a for a in order
                          if not (a == "send-message" and (cross is False or not harnesses["claude-code"]["on_path"]))
                          and not (a == "multiplexer-interactive" and not use_mux)]
    if codex["app_server"] is True:
        adapters["codex"].insert(0, "app-server-steer")
    sessions = None
    if with_sessions:
        res = list_sessions(available)
        sessions = {n: res.get(n, {}).get("sessions", []) for n in LIST_SESSIONS_CMDS}
        errs = {n: v["error"] for n, v in res.items() if "error" in v}
        if errs:
            sessions["error"] = "; ".join(f"{n}: {e}" for n, e in errs.items())
    return {
        "schema": SCHEMA,
        "host_harness": host_harness(env),
        "harnesses": harnesses,
        "multiplexer": {"active": active, "available": available, "pane_id_env_present": pane,
                        "sessions": sessions},
        "claude_cross_session": cross,
        "adapters": adapters,
        "adapter_help": ADAPTER_HELP,
        "claude_address_hint": "address = session display name (--name / /rename), not the --remote-control name",
        "rediscover": "re-run on every resume and before contact; stored bindings are caches",
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
    ap.add_argument("--list-sessions", action="store_true",
                    help="opt-in: run read-only tmux/zellij session listing")
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
    print(json.dumps(build(teams, args.list_sessions), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
