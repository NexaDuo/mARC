#!/usr/bin/env python3
"""No-model probe of the environment a harness gives the model's shell and its hooks (#367).

    python3 scripts/probe_shell_env.py codex   [--cli codex]   [--json OUT]
    python3 scripts/probe_shell_env.py copilot [--cli copilot] [--json OUT]

Installs the plugin from this checkout into a throwaway HOME, points the real
CLI at a local mock model endpoint (Responses API for Codex, chat completions
for Copilot), and has the "model" ask for one shell tool call that prints the
working directory and every variable whose name contains PLUGIN or
PROJECT_DIR. The tool output the CLI sends back is the model shell's
environment, observed rather than assumed. An extra hook appended to the
throwaway installed hooks.json records the hook environment the same way.

No model is called, no token is spent, no secret is read, nothing outside the
temp dir is written. The tool call runs inside the CLI's default sandbox
(Codex: read-only; Copilot: the one shell tool, allowed by name).

Exit codes: 0 probe ran (verdict printed and, with --json, written);
2 the probe could not run (CLI missing, mock not reached): never a pass.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MARK = "MARC_PROBE_367"
PROBE = (f"echo {MARK} cwd=$PWD; env | grep -E 'PLUGIN|PROJECT_DIR' | sed 's/^/{MARK} env /'; "
         f"echo {MARK} end")
WATCH = {"codex": ("PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "PLUGIN_DATA", "CODEX_PROJECT_DIR"),
         "copilot": ("COPILOT_PLUGIN_DATA", "COPILOT_PLUGIN_ROOT", "PLUGIN_ROOT", "COPILOT_PROJECT_DIR")}
USAGE = {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
         "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}


def parse(text: str) -> dict:
    out = {"cwd": None, "env": {}, "complete": False}
    for line in text.splitlines():
        line = line.strip()
        if line.startswith(f"{MARK} cwd="):
            out["cwd"] = line.split("=", 1)[1]
        elif line.startswith(f"{MARK} env "):
            k, _, v = line[len(MARK) + 5:].partition("=")
            out["env"][k] = v
        elif line == f"{MARK} end":
            out["complete"] = True
    return out


class Mock(http.server.BaseHTTPRequestHandler):
    requests: list = []
    tool_outputs: list = []

    def log_message(self, *a):
        pass

    def _send(self, ctype: str, payload: bytes) -> None:
        self.send_response(200)
        self.send_header("content-type", ctype)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._send("application/json", json.dumps(
            {"object": "list", "models": [], "data": [{"id": "mock-model", "object": "model"}]}).encode())

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("content-length", 0))) or b"{}")
        Mock.requests.append(body)
        if "input" in body:  # Codex: Responses API
            outs = [i.get("output") for i in body["input"]
                    if isinstance(i, dict) and i.get("type") == "function_call_output"]
            Mock.tool_outputs += [o if isinstance(o, str) else json.dumps(o) for o in outs]
            tools = [t.get("name") for t in body.get("tools", []) if isinstance(t, dict)]
            if outs or len(Mock.requests) > 3:
                item = {"type": "message", "role": "assistant", "id": "m1",
                        "content": [{"type": "output_text", "text": "done"}]}
            elif "exec_command" in tools:
                item = {"type": "function_call", "id": "f1", "call_id": "c1", "name": "exec_command",
                        "arguments": json.dumps({"cmd": PROBE})}
            else:
                item = {"type": "function_call", "id": "f1", "call_id": "c1", "name": "shell",
                        "arguments": json.dumps({"command": ["bash", "-lc", PROBE]})}
            rid = f"r{len(Mock.requests)}"
            events = [{"type": "response.created", "response": {"id": rid}},
                      {"type": "response.output_item.done", "item": item},
                      {"type": "response.completed", "response": {"id": rid, "usage": USAGE}}]
            self._send("text/event-stream",
                       "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events).encode())
            return
        # Copilot: chat completions
        msgs = body.get("messages", [])
        outs = [m.get("content") for m in msgs if m.get("role") == "tool"]
        Mock.tool_outputs += [o if isinstance(o, str) else json.dumps(o) for o in outs]
        tools = [(t.get("function") or t).get("name") for t in body.get("tools", []) if isinstance(t, dict)]
        if outs or len(Mock.requests) > 3 or "bash" not in tools:
            delta, finish = {"role": "assistant", "content": "done"}, "stop"
        else:
            call = {"index": 0, "id": "c1", "type": "function", "function": {
                "name": "bash", "arguments": json.dumps({"command": PROBE, "description": "probe", "mode": "sync",
                                                          "initial_wait": 10})}}
            delta, finish = {"role": "assistant", "tool_calls": [call]}, "tool_calls"
        base = {"id": "c", "created": 1, "model": "mock-model"}
        usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
        if body.get("stream"):
            c1 = dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": delta, "finish_reason": None}])
            c2 = dict(base, object="chat.completion.chunk", choices=[{"index": 0, "delta": {}, "finish_reason": finish}],
                      usage=usage)
            self._send("text/event-stream", f"data: {json.dumps(c1)}\n\ndata: {json.dumps(c2)}\n\ndata: [DONE]\n\n".encode())
        else:
            msg = dict(delta, content=delta.get("content"))
            self._send("application/json", json.dumps(dict(base, object="chat.completion", usage=usage, choices=[
                {"index": 0, "message": msg, "finish_reason": finish}])).encode())


def add_capture_hook(hooks_json: str, harness: str, capture: str) -> None:
    """Append one env-recording SessionStart hook to the THROWAWAY installed copy."""
    cmd = f"{{ {PROBE}; }} > '{capture}' 2>&1; exit 0"
    with open(hooks_json, encoding="utf-8") as f:
        data = json.load(f)
    if harness == "codex":
        data["hooks"].setdefault("SessionStart", []).append(
            {"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
    else:
        data["hooks"].setdefault("sessionStart", []).append({"type": "command", "timeoutSec": 15, "bash": cmd})
    with open(hooks_json, "w", encoding="utf-8") as f:
        json.dump(data, f)


def probe(harness: str, cli: str) -> dict:
    Mock.requests, Mock.tool_outputs = [], []
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), Mock)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    base = tempfile.mkdtemp(prefix=f"marc-{harness}-envprobe-")
    try:
        home, ws = os.path.join(base, "home"), os.path.join(base, "ws")
        os.makedirs(home)
        os.makedirs(ws)
        capture = os.path.join(base, "hook-env.txt")
        env = {k: v for k, v in os.environ.items()
               if not any(s in k for s in ("TOKEN", "API_KEY", "PLUGIN", "PROJECT_DIR", "COPILOT", "CODEX", "GH_"))}
        env["HOME"] = home

        def run(*args, timeout=240):
            return subprocess.run([cli, *args], cwd=ws, env=env, capture_output=True, text=True, timeout=timeout)

        if harness == "codex":
            env["CODEX_HOME"] = os.path.join(home, ".codex")
            os.makedirs(env["CODEX_HOME"])
            run("plugin", "marketplace", "add", ROOT, "--json")
            installed = json.loads(run("plugin", "add", "marc@nexaduo", "--json").stdout)["installedPath"]
            add_capture_hook(os.path.join(installed, "hooks", "hooks.json"), harness, capture)
            cfg = ["-c", "model_provider=mock", "-c", f'model_providers.mock.base_url="{url}"',
                   "-c", 'model_providers.mock.name="mock"', "-c", 'model_providers.mock.wire_api="responses"',
                   "-c", "model_providers.mock.requires_openai_auth=false", "-c", 'model="mock-model"']
            r = run("exec", *cfg, "--skip-git-repo-check", "-s", "read-only", "--enable", "codex_hooks", "probe")
        else:
            run("plugin", "marketplace", "add", ROOT)
            run("plugin", "install", "marc@nexaduo")
            installed = os.path.join(home, ".copilot", "installed-plugins", "nexaduo", "marc")
            add_capture_hook(os.path.join(installed, "hooks", "hooks.json"), harness, capture)
            env.update(COPILOT_PROVIDER_BASE_URL=url, COPILOT_PROVIDER_TYPE="openai", COPILOT_PROVIDER_API_KEY="mock",
                       COPILOT_OFFLINE="true", COPILOT_MODEL="mock-model", COPILOT_PROVIDER_MODEL_ID="mock-model")
            r = run("-p", "probe", "--model", "mock-model", "--allow-tool", "shell", "--no-color")
        shell = parse("\n".join(Mock.tool_outputs))
        hook = {"cwd": None, "env": {}, "complete": False}
        if os.path.exists(capture):
            with open(capture, encoding="utf-8") as f:
                hook = parse(f.read())
        return {"harness": harness, "cli_rc": r.returncode, "installed": installed, "workspace": ws,
                "home": home, "model_requests": len(Mock.requests), "model_shell": shell, "hook": hook,
                "stderr_tail": r.stderr[-600:]}
    finally:
        srv.shutdown()
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("harness", choices=sorted(WATCH))
    ap.add_argument("--cli")
    ap.add_argument("--json")
    a = ap.parse_args()
    cli = shutil.which(a.cli or a.harness)
    if not cli:
        print(f"PROBE NOT RUN: `{a.cli or a.harness}` not on PATH", file=sys.stderr)
        return 2
    res = probe(a.harness, cli)
    shell, hook = res["model_shell"], res["hook"]
    if not shell["complete"]:
        print(f"PROBE NOT RUN: the mock never received the shell tool output "
              f"(rc={res['cli_rc']}, requests={res['model_requests']}): {res['stderr_tail']!r}", file=sys.stderr)
        return 2
    print(f"{a.harness}: model shell cwd {'== workspace' if shell['cwd'] == res['workspace'] else shell['cwd']}")
    for var in WATCH[a.harness]:
        print(f"{a.harness}: model shell {var}: {'EXPORTED' if var in shell['env'] else 'not exported'}")
    if hook["complete"]:
        print(f"{a.harness}: hook cwd {'== workspace' if hook['cwd'] == res['workspace'] else hook['cwd']}")
        for var in WATCH[a.harness]:
            print(f"{a.harness}: hook {var}: {'EXPORTED' if var in hook['env'] else 'not exported'}")
    else:
        print(f"{a.harness}: hook env not captured (the CLI did not run plugin SessionStart hooks here)")
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(res, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
