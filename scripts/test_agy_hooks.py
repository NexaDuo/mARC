#!/usr/bin/env python3
"""Antigravity hook contract test (origin: #343). Stdlib only, no model call.

    python3 scripts/test_agy_hooks.py [PLUGIN_DIR]   # default: harnesses/antigravity/marc

Runs every hooks.json command the way Antigravity does, as probed on agy 1.3.5
(Windows) and 1.3.2 (Linux) and documented inside the agy binary:
  * `sh -c "<command>"` (`cmd /c` on Windows), working directory = the folder
    holding hooks.json, no AGY_PLUGIN_ROOT/AGY_PROJECT_DIR in the environment,
    OLDPWD unrelated to the workspace;
  * a camelCase JSON object on stdin, with the workspace in `workspacePaths`;
  * a JSON object required on stdout (plain text is rejected).
And checks that each command is a plain word list cmd.exe passes through
untouched (`bash hooks/agy-hook.sh <id> <Event>`), that SessionStart text
reaches injectSteps, and that the project's team.toml is the one found.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVENTS = {"SessionStart", "PreInvocation", "PostInvocation", "PreToolUse", "PostToolUse", "Stop"}
COMMAND_RE = re.compile(r"bash hooks/agy-hook\.sh ([a-z0-9-]+) (" + "|".join(sorted(EVENTS)) + r")")
MARKER = "AGY-CONTRACT-MARKER-343"

failures: list[str] = []


def check(ok: bool, msg: str) -> None:
    print(("PASS: " if ok else "FAIL: ") + msg)
    if not ok:
        failures.append(msg)


def handlers(hooks: dict):
    for hook_id, events in hooks.items():
        for event, entries in events.items():
            if event == "enabled":
                continue
            for entry in entries:
                for h in entry.get("hooks", [entry]):
                    yield hook_id, event, h.get("command", "")


def main() -> int:
    plugin = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else ROOT / "harnesses" / "antigravity" / "marc"
    hooks = json.loads((plugin / "hooks.json").read_text(encoding="utf-8"))
    entries = list(handlers(hooks))
    check(bool(entries), f"{plugin}/hooks.json declares hook handlers")

    with tempfile.TemporaryDirectory() as ws:
        (Path(ws) / ".agents").mkdir()
        (Path(ws) / ".agents" / "team.toml").write_text(f'[github]\ngh_org = "x"\nmarker = "{MARKER}"\n')
        stdin = json.dumps({"conversationId": "contract-test", "workspacePaths": [ws],
                            "transcriptPath": "/nonexistent", "modelName": "auto"})
        state = os.path.join(ws, ".state")  # removed with the workspace
        env = {k: v for k, v in os.environ.items() if not k.startswith(("AGY_", "CLAUDE_"))}
        env.update({"OLDPWD": "/", "MARC_STATE_DIR": state, "HOME": state})
        for hook_id, event, command in entries:
            m = COMMAND_RE.fullmatch(command)
            plain = bool(m) and m.group(1) == hook_id and m.group(2) == event
            check(plain, f"{hook_id}/{event}: command is the plain dispatcher form ({command!r})")
            if not plain:
                continue  # never hand an unexpected command to a shell
            check((plugin / "hooks" / "agy" / f"{hook_id}.sh").is_file(),
                  f"{hook_id}: hooks/agy/{hook_id}.sh is shipped")
            r = subprocess.run(["sh", "-c", command], cwd=plugin, input=stdin, capture_output=True,
                               text=True, env=env, timeout=60)
            check(r.returncode == 0, f"{hook_id}: exits 0 (got {r.returncode})")
            check("not found" not in r.stderr, f"{hook_id}: no 'not found' on stderr ({r.stderr.strip()[:160]!r})")
            try:
                out = json.loads(r.stdout)
            except ValueError:
                out = None
            check(isinstance(out, dict), f"{hook_id}: stdout is one JSON object ({r.stdout.strip()[:120]!r})")
            if hook_id == "team-toml-dump" and isinstance(out, dict):
                steps = out.get("injectSteps") or [{}]
                text = steps[0].get("systemMessage", {}).get("systemMessage", "")
                check(MARKER in text, f"{hook_id}: the workspace's team.toml reaches injectSteps")
                check(text.startswith("[mARC] The block below is this repository's own configuration"),
                      f"{hook_id}: repo-supplied text is framed as data")
    if failures:
        print(f"\n{len(failures)} failure(s)")
        return 1
    print("\nAntigravity hook contract: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
