#!/usr/bin/env python3
"""Installed-copy hook + bundled-script matrix, one adapter per harness (#367).

    python3 scripts/test_installed_harness.py HARNESS [--plugin DIR] [--probe-json F]
                                               [--source] [--seed-break KIND]

HARNESS is claude-code, codex, copilot or antigravity. Stdlib only, no model
call, no secret, no token cost. For each harness it proves, on the INSTALLED
copy (installed here into a throwaway HOME with the real CLI, or the copy at
--plugin), two things:

1. Hooks. Every command in the installed hooks.json runs the way that CLI runs
   it: the shell, the working directory and the environment variables the CLI
   really sets (and only those), and a realistic stdin payload per event. Each
   must exit 0, print no `[mARC] hook script not found`, and produce the
   expected output (team.toml reaches the session, the compact card prints,
   the outdated-check body really runs, Antigravity gets one JSON object).
2. Bundled scripts. Every `python3 "<dir>/<name>.py"` reference in the
   installed skills/agents is executed from a cwd holding a decoy
   ./scripts/<name>.py AND a decoy ./json.py, with the environment the
   model's shell really gets. It must resolve to the installed copy, and no
   decoy may run. Zero references found is a failure.

What each CLI really provides (evidence, see also scripts/probe_shell_env.py):
  * Claude Code: hooks get CLAUDE_PLUGIN_ROOT, CLAUDE_PROJECT_DIR, cwd = the
    project (documented). The model's Bash tool gets neither variable
    (observed: env of a Claude Code 2.1.291 session with marc installed).
  * Codex: hooks get PLUGIN_ROOT, CLAUDE_PLUGIN_ROOT, PLUGIN_DATA,
    CLAUDE_PLUGIN_DATA, run via `<shell> -lc` (openai/codex
    codex-rs/hooks/src/engine/discovery.rs, command_runner.rs); cwd = the
    session cwd (assumed). The model's shell gets none of them (observed by
    probe_shell_env.py on 0.156.1 and 0.162.1).
  * Copilot: hooks get PLUGIN_ROOT, COPILOT_PLUGIN_ROOT, CLAUDE_PLUGIN_ROOT,
    COPILOT_PLUGIN_DATA, CLAUDE_PLUGIN_DATA, COPILOT_PROJECT_DIR,
    CLAUDE_PROJECT_DIR, cwd = the installed plugin dir; the model's shell
    gets none of them (all observed by probe_shell_env.py on 1.0.72).
  * Antigravity: `sh -c`, cwd = the folder holding hooks.json, no AGY_* env,
    workspace only in the stdin JSON, a JSON object required on stdout
    (#343); the model's shell gets no plugin root (#364).

--probe-json feeds a probe_shell_env.py result in; the test then checks the
observed CLI behaviour still matches the model above, so a CLI upgrade that
changes it fails here instead of silently drifting.

--source tests hooks only, against harnesses/<h>/marc in this checkout.
--seed-break proves the assertions can fail (for the PR record, never CI):
  drop-hook-env     hooks run without the CLI's plugin-root variables
  drop-hook-script  one hook body is deleted from (a copy of) the install
  cwd-fallback      references resolve with the old `${VAR:-.}/scripts`
  export-root       the model shell gets the plugin root (decoy check must
                    still pass; proves the test doesn't depend on it)
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import compile_prompts  # noqa: E402

MARKER = "MARC-INSTALLED-367"
FAKE_REMOTE_VERSION = "999.0.0"
NOT_FOUND = "hook script not found"
CWD_FALLBACK = re.compile(r"\$\{[A-Za-z_]+:-(\.|\$PWD)\}/scripts|(^|[\s\"'(])\./scripts/")
STRIP = ("PLUGIN", "PROJECT_DIR", "AGY_", "TOKEN", "API_KEY")

failures: list[str] = []
xfails: list[str] = []
HARNESS = ""

# Known, tracked defects: (harness, full message prefix) -> issue. The key is
# the whole check message up to the first "(", so a reworded message stops
# matching and fails red. An XFAIL is printed and counted, never hidden; if
# the check starts passing (XPASS) that is a failure, so the entry gets
# removed in the fixing PR.
XFAIL = {
    ("codex", "team-toml@SessionStart[*]: the workspace's team.toml reaches the session"):
        "https://github.com/NexaDuo/mARC/issues/370",
}


def check(ok: bool, msg: str) -> bool:
    issue = next((u for (h, p), u in XFAIL.items() if h == HARNESS and msg.split(" (")[0] == p), None)
    if issue and not ok:
        print(f"XFAIL ({issue}): {msg}")
        xfails.append(f"{msg} [{issue}]")
        return ok
    if issue and ok:
        msg = f"XPASS, remove the XFAIL entry for {issue}: {msg}"
    print(("PASS: " if ok and not issue else "FAIL: ") + msg)
    if not ok or issue:
        failures.append(msg)
    return ok


def sh(argv, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 120)
    return subprocess.run(argv, **kw)


def base_env(extra: dict) -> dict:
    # Config-dir overrides of the developer's real CLIs never leak in (#371 SEC-2).
    env = {k: v for k, v in os.environ.items() if not any(s in k for s in STRIP) and not k.startswith("CLAUDE")
           and k not in ("COPILOT_HOME", "XDG_CONFIG_HOME", "CODEX_HOME")}
    env.update(extra)
    return env


def payload(harness: str, event: str, ws: str, source: str = "startup") -> str:
    if harness == "copilot":  # camelCase, Copilot hook payloads
        d = {"timestamp": 1760000000000, "cwd": ws, "sessionId": "installed-test"}
        if event == "sessionStart":
            d.update(source="new", initialPrompt="hello")
        else:
            d.update(toolName="bash", toolArgs=json.dumps({"command": "ls"}),
                     toolResult={"resultType": "success", "textResultForLlm": "ok"})
        return json.dumps(d)
    d = {"session_id": "installed-test", "transcript_path": os.path.join(ws, "no-transcript.jsonl"),
         "cwd": ws, "hook_event_name": event, "permission_mode": "default"}
    if event == "SessionStart":
        d["source"] = source
        if harness == "codex":
            d["model"] = "mock-model"
    elif event in ("PreToolUse", "PostToolUse"):
        d.update(tool_name="Bash", tool_input={"command": "ls"}, tool_use_id="t1")
        if event == "PostToolUse":
            d["tool_response"] = {"stdout": "ok", "stderr": "", "interrupted": False}
    elif event == "Stop":
        d["stop_hook_active"] = False
    return json.dumps(d)


# --------------------------------------------------------------------------- adapters
class Adapter:
    name = ""
    cli = ""

    def __init__(self, tmp: str):
        self.tmp = tmp
        self.ctx: dict = {}  # install-context env (HOME, CODEX_HOME, ...) the user's shell also has

    def install(self) -> str:
        raise NotImplementedError

    def hooks(self, plugin: str):
        """Yield (label, event, matcher, command)."""
        raise NotImplementedError

    def hook_run(self, plugin: str, ws: str, event: str, matcher: str, command: str, seed: str):
        raise NotImplementedError

    def hook_expect(self, label: str, event: str, matcher: str, out: str) -> None:
        pass

    def compile_config(self) -> dict:
        with open(os.path.join(ROOT, "harnesses", self.name, "marc", "compile.json"), encoding="utf-8") as f:
            return compile_prompts.apply_derived_defaults(json.load(f))

    def model_shell_env(self, plugin: str, seed: str) -> dict:
        env = base_env(self.ctx)
        if seed == "export-root":
            env[self.compile_config()["plugin_root_env"]] = plugin
        return env


def claude_style_hooks(plugin: str, rel: str):
    with open(os.path.join(plugin, rel), encoding="utf-8") as f:
        data = json.load(f)
    for event, entries in data["hooks"].items():
        for entry in entries:
            for h in entry.get("hooks", []):
                cmd = h.get("command", "")
                m = re.search(r"/(?:hooks|scripts)/([A-Za-z0-9_.-]+)", cmd)
                label = m.group(1) if m else ("team-toml" if "team.toml" in cmd else "inline")
                yield f"{label}@{event}[{entry.get('matcher', '*')}]", event, entry.get("matcher", "*"), cmd


class ClaudeCode(Adapter):
    name, cli = "claude-code", "claude"

    def install(self) -> str:
        self.ctx = {"CLAUDE_CONFIG_DIR": os.path.join(self.tmp, "claude-config"), "HOME": os.path.join(self.tmp, "home")}
        for d in self.ctx.values():
            os.makedirs(d, exist_ok=True)
        env = base_env(self.ctx)
        sh(["claude", "plugin", "marketplace", "add", ROOT], env=env, cwd=self.tmp, check=True)
        sh(["claude", "plugin", "install", "marc@nexaduo"], env=env, cwd=self.tmp, check=True)
        listing = json.loads(sh(["claude", "plugin", "list", "--json"], env=env, cwd=self.tmp, check=True).stdout)
        return next(p["installPath"] for p in listing if p.get("id") == "marc@nexaduo")

    def hooks(self, plugin):
        return claude_style_hooks(plugin, "hooks/hooks.json")

    def hook_run(self, plugin, ws, event, matcher, command, seed):
        env = base_env(self.ctx)
        if seed != "drop-hook-env":
            env.update(CLAUDE_PLUGIN_ROOT=plugin, CLAUDE_PROJECT_DIR=ws)
        src = "compact" if matcher == "compact" else "startup"
        return sh(["bash", "-c", command], cwd=ws, env=env, input=payload("claude", event, ws, src))

    def hook_expect(self, label, event, matcher, out):
        claude_like_expect(label, matcher, out)


def claude_like_expect(label: str, matcher: str, out: str) -> None:
    if label.startswith("team-toml"):
        check(MARKER in out, f"{label}: the workspace's team.toml reaches the session")
    if label.startswith("invariants-card"):
        check("invariant" in out.lower(), f"{label}: the compact invariants card is printed")
    if label.startswith("outdated-check"):
        check(FAKE_REMOTE_VERSION in out, f"{label}: the outdated-check body really ran (saw {FAKE_REMOTE_VERSION})")
    if out.lstrip().startswith("{"):
        try:
            json.loads(out)
            ok = True
        except ValueError:
            ok = False
        check(ok, f"{label}: JSON-looking stdout parses as JSON")


class Codex(Adapter):
    name, cli = "codex", "codex"

    def install(self) -> str:
        self.ctx = {"CODEX_HOME": os.path.join(self.tmp, "codex-home"), "HOME": os.path.join(self.tmp, "home")}
        for d in self.ctx.values():
            os.makedirs(d, exist_ok=True)
        env = base_env(self.ctx)
        sh(["codex", "plugin", "marketplace", "add", ROOT, "--json"], env=env, cwd=self.tmp, check=True)
        out = sh(["codex", "plugin", "add", "marc@nexaduo", "--json"], env=env, cwd=self.tmp, check=True).stdout
        return json.loads(out)["installedPath"]

    def hooks(self, plugin):
        return claude_style_hooks(plugin, "hooks/hooks.json")

    def hook_run(self, plugin, ws, event, matcher, command, seed):
        env = base_env(self.ctx)
        if seed != "drop-hook-env":
            data = os.path.join(self.ctx["CODEX_HOME"], "plugins", "data", "marc-nexaduo")
            env.update(PLUGIN_ROOT=plugin, CLAUDE_PLUGIN_ROOT=plugin, PLUGIN_DATA=data, CLAUDE_PLUGIN_DATA=data)
        src = "compact" if matcher == "compact" else "startup"
        return sh(["bash", "-lc", command], cwd=ws, env=env, input=payload("codex", event, ws, src))

    def hook_expect(self, label, event, matcher, out):
        claude_like_expect(label, matcher, out)


class Copilot(Adapter):
    name, cli = "copilot", "copilot"

    def install(self) -> str:
        home = os.path.join(self.tmp, "home")
        os.makedirs(home)
        self.ctx = {"HOME": home, "COPILOT_HOME": os.path.join(home, ".copilot")}
        env = base_env(self.ctx)
        sh(["copilot", "plugin", "marketplace", "add", ROOT], env=env, cwd=self.tmp, check=True)
        sh(["copilot", "plugin", "install", "marc@nexaduo"], env=env, cwd=self.tmp, check=True)
        listing = sh(["copilot", "plugin", "list"], env=env, cwd=self.tmp).stdout
        check("marc@nexaduo" in listing, "copilot: marc@nexaduo installed into the temp COPILOT_HOME")
        return os.path.join(home, ".copilot", "installed-plugins", "nexaduo", "marc")

    def hooks(self, plugin):
        with open(os.path.join(plugin, "hooks", "hooks.json"), encoding="utf-8") as f:
            data = json.load(f)
        check(data.get("version") == 1 and isinstance(data.get("hooks"), dict), "copilot hooks.json: version 1, hooks object")
        for event, entries in data["hooks"].items():
            for i, h in enumerate(entries):
                cmd = h.get("bash", "")
                label = ("team-toml" if "team.toml" in cmd else "script-copy" if "cp -f" in cmd
                         else "token-sentinel" if "token_sentinel" in cmd else f"hook{i}")
                yield f"{label}@{event}", event, "*", cmd

    def plugin_data(self) -> str:
        return os.path.join(self.ctx["HOME"], ".copilot", "plugin-data", "nexaduo", "marc")

    def hook_run(self, plugin, ws, event, matcher, command, seed):
        env = base_env(self.ctx)
        if seed != "drop-hook-env":
            env.update(PLUGIN_ROOT=plugin, COPILOT_PLUGIN_ROOT=plugin, CLAUDE_PLUGIN_ROOT=plugin,
                       COPILOT_PLUGIN_DATA=self.plugin_data(), CLAUDE_PLUGIN_DATA=self.plugin_data(),
                       COPILOT_PROJECT_DIR=ws, CLAUDE_PROJECT_DIR=ws)
        return sh(["bash", "-c", command], cwd=plugin, env=env, input=payload("copilot", event, ws))

    def hook_expect(self, label, event, matcher, out):
        if label.startswith("team-toml"):
            check(MARKER in out, f"{label}: the workspace's team.toml reaches the session")
        if label.startswith("script-copy"):
            check(os.path.isfile(os.path.join(self.plugin_data(), "scripts", "token_sentinel.py")),
                  f"{label}: scripts land in the CLI's COPILOT_PLUGIN_DATA ({self.plugin_data()})")


class Antigravity(Adapter):
    name, cli = "antigravity", "agy"
    EVENTS = ("SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop")
    COMMAND_RE = re.compile(r"bash hooks/agy-hook\.sh ([a-z0-9-]+) (" + "|".join(sorted(EVENTS)) + r")")

    def install(self) -> str:
        raise SystemExit("antigravity: pass --plugin <installed dir> (CI installs it with the gated secret)")

    def hooks(self, plugin):
        with open(os.path.join(plugin, "hooks.json"), encoding="utf-8") as f:
            data = json.load(f)
        for hook_id, events in data.items():
            for event, entries in events.items():
                if event == "enabled":
                    continue
                for entry in entries:
                    for h in entry.get("hooks", [entry]):
                        yield hook_id, event, "*", h.get("command", "")

    def hook_run(self, plugin, ws, event, matcher, command, seed):
        m = self.COMMAND_RE.fullmatch(command)
        if not check(bool(m) and m.group(2) == event, f"{event}: command is the plain dispatcher form ({command!r})"):
            return None  # never hand an unexpected command to a shell
        env = base_env(self.ctx)
        env["OLDPWD"] = "/"
        if seed == "drop-hook-env":  # agy sets no env at all; the break is the cwd (#343)
            return sh(["sh", "-c", command], cwd=ws, env=env, input=self.stdin(ws))
        return sh(["sh", "-c", command], cwd=plugin, env=env, input=self.stdin(ws))

    @staticmethod
    def stdin(ws):
        return json.dumps({"conversationId": "installed-test", "workspacePaths": [ws],
                           "transcriptPath": "/nonexistent", "modelName": "auto"})

    def hook_expect(self, label, event, matcher, out):
        try:
            obj = json.loads(out)
        except ValueError:
            obj = None
        check(isinstance(obj, dict), f"{label}: stdout is one JSON object ({out.strip()[:100]!r})")
        if label == "team-toml-dump" and isinstance(obj, dict):
            steps = obj.get("injectSteps") or [{}]
            text = steps[0].get("systemMessage", {}).get("systemMessage", "")
            check(MARKER in text, f"{label}: the workspace's team.toml reaches injectSteps")
            check(text.startswith("[mARC] The block below is this repository's own configuration"),
                  f"{label}: repo-supplied text is framed as data")


ADAPTERS = {a.name: a for a in (ClaudeCode, Codex, Copilot, Antigravity)}


# --------------------------------------------------------------------------- phases
def run_hooks(ad: Adapter, plugin: str, seed: str) -> None:
    entries = list(ad.hooks(plugin))
    check(bool(entries), f"{ad.name}: installed hooks.json declares hook handlers ({len(entries)})")
    ws = os.path.join(ad.tmp, "hook-ws")
    state = os.path.join(ad.tmp, "hook-state")
    os.makedirs(os.path.join(ws, ".agents"), exist_ok=True)
    with open(os.path.join(ws, ".agents", "team.toml"), "w", encoding="utf-8") as f:
        f.write(f'[github]\ngh_org = "x"\nmarker = "{MARKER}"\n')
    remote = os.path.join(ad.tmp, "remote-plugin.json")
    with open(remote, "w", encoding="utf-8") as f:
        json.dump({"name": "marc", "version": FAKE_REMOTE_VERSION}, f)
    # Hook side effects stay in the temp dir; the version check reads a local
    # file instead of the network, and reports a newer version so the test
    # can see the outdated-check body actually ran.
    ad.ctx.setdefault("HOME", os.path.join(ad.tmp, "home"))
    os.makedirs(ad.ctx["HOME"], exist_ok=True)
    # OLDPWD: a CLI inherits whatever the user's shell exported, usually the
    # previous directory, never reliably the project (#370).
    ad.ctx.update(MARC_STATE_DIR=state, MARC_REMOTE_PLUGIN_JSON_URL="file://" + remote, OLDPWD="/")
    for label, event, matcher, command in entries:
        r = ad.hook_run(plugin, ws, event, matcher, command, seed)
        if r is None:
            continue
        check(r.returncode == 0, f"{label}: exits 0 (got {r.returncode})")
        check(NOT_FOUND not in r.stderr + r.stdout and "not found" not in r.stderr,
              f"{label}: no '[mARC] {NOT_FOUND}' ({r.stderr.strip()[:160]!r})")
        ad.hook_expect(label, event, matcher, r.stdout)
    for k in ("MARC_STATE_DIR", "MARC_REMOTE_PLUGIN_JSON_URL", "OLDPWD"):
        ad.ctx.pop(k, None)


INVOCATION = re.compile(r'python3 "(.+?)/([A-Za-z0-9_.\-]+\.py)"')


def run_scripts(ad: Adapter, plugin: str, seed: str) -> None:
    expr = ad.compile_config()["bundled_scripts_dir"]
    texts = {}
    for sub in ("skills", "agents"):
        for path in glob.glob(os.path.join(plugin, sub, "**", "*.md"), recursive=True):
            with open(path, encoding="utf-8") as f:
                texts[os.path.relpath(path, plugin)] = f.read()
    # A file may resolve the dir once (`S="<resolver>"`, #378: each expansion
    # costs ~2 KB of context) and invoke `python3 "$S/<name>.py"`; `$S` counts
    # only in a file that assigns it the resolver itself.
    refs = sorted({(expr if (e == "$S" and f'S="{expr}"' in t) else e, n)
                   for t in texts.values() for e, n in INVOCATION.findall(t)})
    names = sorted({n for _, n in refs})
    check(len(names) >= 3, f"{ad.name}: bundled-script references found in the installed skills: {names}")
    for rel, text in texts.items():
        if CWD_FALLBACK.search(text):
            check(False, f"{ad.name}: {rel} carries a cwd fallback for bundled scripts")
    check(all(e == expr for e, _ in refs), f"{ad.name}: every reference uses the compiled resolver expression")
    if seed == "cwd-fallback":
        expr = "${%s:-.}/scripts" % ad.compile_config()["plugin_root_env"]
    env = ad.model_shell_env(plugin, seed)
    marker = os.path.join(ad.tmp, "decoy-ran")
    ws = os.path.join(ad.tmp, "decoy-ws")
    os.makedirs(os.path.join(ws, "scripts"), exist_ok=True)
    with open(os.path.join(ws, "json.py"), "w", encoding="utf-8") as f:
        f.write(f"open({marker!r}, 'a').write('json.py ')\nraise SystemExit('decoy json.py ran')\n")
    want = os.path.realpath(os.path.join(plugin, "scripts"))
    for name in names:
        with open(os.path.join(ws, "scripts", name), "w", encoding="utf-8") as f:
            f.write(f"open({marker!r}, 'a').write('scripts/{name} ')\nprint('DECOY')\n")
        got = sh(["bash", "-c", f'printf "%s" "{expr}"'], cwd=ws, env=env)
        resolved = os.path.realpath(os.path.join(ws, got.stdout)) if got.stdout else ""
        check(resolved == want, f"{ad.name}: {name}: resolves to the installed copy from a decoy cwd "
                                f"(got {got.stdout!r}, err {got.stderr.strip()[:200]!r})")
        r = sh(["bash", "-c", f'python3 "{expr}/{name}" --help'], cwd=ws, env=env)
        ran = open(marker).read() if os.path.exists(marker) else ""
        if os.path.exists(marker):
            os.remove(marker)
        check(r.returncode == 0 and "DECOY" not in r.stdout,
              f"{ad.name}: {name} --help runs from the installed copy (rc={r.returncode}, err {r.stderr.strip()[:160]!r})")
        check(not ran, f"{ad.name}: {name}: no workspace decoy ran ({ran.strip() or 'none'})")


def check_probe(ad: Adapter, path: str) -> None:
    with open(path, encoding="utf-8") as f:
        p = json.load(f)
    shell, hook = p["model_shell"], p["hook"]
    root_env = ad.compile_config()["plugin_root_env"]
    check(shell.get("complete") is True, f"{ad.name} probe: the model shell's env was observed")
    check(root_env not in shell["env"],
          f"{ad.name} probe: model shell has no {root_env} (the bug class #367 guards; if this flips, "
          f"the CLI started exporting it: revisit, the resolver still works)")
    if ad.name == "copilot":
        want = {"PLUGIN_ROOT", "COPILOT_PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT", "COPILOT_PLUGIN_DATA",
                "CLAUDE_PLUGIN_DATA", "COPILOT_PROJECT_DIR", "CLAUDE_PROJECT_DIR"}
        check(hook.get("complete") is True, "copilot probe: the hook env was observed")
        check(want <= set(hook["env"]), f"copilot probe: hooks get {sorted(want)} (missing {sorted(want - set(hook['env']))})")
        check(hook.get("cwd") == p["installed"], "copilot probe: hooks run with cwd = the installed plugin dir")
        check(hook["env"].get("COPILOT_PLUGIN_DATA", "").endswith(os.path.join("plugin-data", "nexaduo", "marc")),
              "copilot probe: COPILOT_PLUGIN_DATA is <home>/.copilot/plugin-data/<marketplace>/marc")
    elif hook.get("complete"):
        check({"PLUGIN_ROOT", "CLAUDE_PLUGIN_ROOT"} <= set(hook["env"]), f"{ad.name} probe: hooks get PLUGIN_ROOT")
    else:
        print(f"NOTE: {ad.name} probe: the CLI ran no plugin hook in a headless session; "
              "the hook env above comes from the CLI source, not observation")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("harness", choices=sorted(ADAPTERS))
    ap.add_argument("--plugin", help="an already-installed copy (default: install into a temp HOME)")
    ap.add_argument("--source", action="store_true", help="hooks only, against this checkout's harness dir")
    ap.add_argument("--probe-json")
    ap.add_argument("--seed-break", default="", choices=["", "drop-hook-env", "drop-hook-script", "cwd-fallback", "export-root"])
    a = ap.parse_args()
    global HARNESS
    HARNESS = a.harness
    with tempfile.TemporaryDirectory(prefix=f"marc-{a.harness}-installed-") as tmp:
        ad = ADAPTERS[a.harness](tmp)
        if a.source:
            plugin = os.path.join(ROOT, "harnesses", a.harness, "marc")
        elif a.plugin:
            plugin = os.path.realpath(a.plugin)
            ad.ctx = {k: os.environ[k] for k in ("HOME", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "COPILOT_HOME", "PATH")
                      if k in os.environ}
        else:
            if not shutil.which(ad.cli):
                print(f"FAIL: `{ad.cli}` is not on PATH, nothing was tested")
                return 1
            plugin = ad.install()
        check(os.path.isdir(plugin), f"{a.harness}: plugin dir {plugin}")
        if a.seed_break == "drop-hook-script":
            if a.plugin or a.source:  # never touch a copy this run did not install
                shutil.copytree(plugin, os.path.join(tmp, "broken-copy"), symlinks=True)
                plugin = os.path.join(tmp, "broken-copy")
            victim = {"antigravity": "hooks/agy/outdated-check.sh", "copilot": "scripts/token_sentinel.py"}.get(
                a.harness, "hooks/invariants-card.sh")
            print(f"SEED: deleting {victim}")
            os.remove(os.path.join(plugin, victim))
        print(f"== {a.harness}: hooks, run the way the CLI runs them ({plugin})")
        run_hooks(ad, plugin, a.seed_break)
        if not a.source:
            print(f"== {a.harness}: bundled scripts from a decoy cwd, with the model shell's env")
            run_scripts(ad, plugin, a.seed_break)
        if a.probe_json:
            print(f"== {a.harness}: observed CLI behaviour still matches this test's model")
            check_probe(ad, a.probe_json)
    if failures:
        print(f"\n{len(failures)} failure(s):")
        for f in failures:
            print(f"  - {f}")
        return 1
    for x in xfails:
        print(f"XFAIL (tracked, not a pass): {x}")
    print(f"\n{a.harness} installed-copy matrix: OK" + (f" with {len(xfails)} tracked XFAIL(s)" if xfails else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
