#!/usr/bin/env python3
"""Boundary probe for the Codex read-only reviewer profile (origin: #351 · 2026-10-14).

Runs a fixed battery of side effects under `codex sandbox -P <profile>`, with the
exact permissions profile dispatch_agent.py passes to `codex exec` for opted-in
read-only roles (CODEX_READONLY_PROFILE_ARGS, imported, not copied), and asserts
the expected matrix:

    read_file         allowed   (a reviewer must read the checkout)
    write_workspace   blocked   (./ of the checkout)
    write_tmp         blocked   (/tmp)
    write_home        blocked   ($HOME)
    write_codex_home  blocked   (${CODEX_HOME:-~/.codex}, i.e. the Codex config)
    git_commit        blocked   (`git commit --allow-empty` in a throwaway repo)
    network           allowed   (`curl https://api.github.com`)

The same battery first runs OUTSIDE the sandbox as a control: every probe must be
allowed there, so a "blocked" result inside the sandbox is the sandbox's doing and
not a missing directory or tool. A blocked write is confirmed twice: non-zero exit
inside the sandbox AND no file left behind afterwards.

No model call, no secret, no token. Stdlib only.

Usage:
    python3 scripts/probe_codex_readonly.py            # sandbox probe (CI default)
    python3 scripts/probe_codex_readonly.py --json     # machine-readable result
    python3 scripts/probe_codex_readonly.py --exec     # OWNER ONLY, manual: real
        `codex exec` (one model call) with the same profile, to check that exec
        honors it, also with a user config.toml carrying
        `sandbox_mode = "danger-full-access"`. Never run in CI.

Exit codes: 0 matrix as expected; 1 matrix changed (a Codex upgrade changed the
profile semantics, or the probe found a hole); 2 the probe could not run (codex
missing, sandbox failed to start, control run not clean).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "core" / "scripts"))
from dispatch_agent import CODEX_READONLY_PROFILE, CODEX_READONLY_PROFILE_ARGS  # noqa: E402

EXPECTED: Dict[str, str] = {
    "read_file": "allowed",
    "write_workspace": "blocked",
    "write_tmp": "blocked",
    "write_home": "blocked",
    "write_codex_home": "blocked",
    "git_commit": "blocked",
    "network": "allowed",
}
SENTINEL = "MARC_PROBE_BEGIN"

# One bash script, one line per probe: `<name>=<exit code>`. Paths come from
# the environment so the identical script runs in the control and the sandbox.
PROBE_SH = r"""
set -u
echo MARC_PROBE_BEGIN
run() { name=$1; shift; "$@" >/dev/null 2>&1; echo "$name=$?"; }
run read_file cat "$MARC_PROBE_READ"
run write_workspace sh -c 'echo x > "$MARC_PROBE_WS"'
run write_tmp sh -c 'echo x > "$MARC_PROBE_TMP"'
run write_home sh -c 'echo x > "$MARC_PROBE_HOME"'
run write_codex_home sh -c 'echo x > "$MARC_PROBE_CODEX"'
run git_commit git -C "$MARC_PROBE_REPO" -c user.name=marc-probe -c user.email=probe@invalid \
    -c commit.gpgsign=false -c core.hooksPath=/dev/null commit -q --allow-empty -m probe
net() {
  for _ in 1 2 3; do
    curl -sS -o /dev/null --max-time 15 https://api.github.com && return 0
    sleep 2
  done
  return 1
}
run network net
echo MARC_PROBE_END
"""


def _git(args: List[str], cwd: Path) -> None:
    subprocess.run(["git", "-c", "init.defaultBranch=main", "-c", "commit.gpgsign=false", *args],
                   cwd=str(cwd), check=True, capture_output=True)


class Battery:
    """Fresh targets for one run: a checkout dir, a throwaway repo, unique write paths."""

    def __init__(self, base: Path, tag: str) -> None:
        self.ws = base / f"ws-{tag}"
        self.ws.mkdir()
        (self.ws / "README.md").write_text("probe target\n", encoding="utf-8")
        (self.ws / "probe.sh").write_text(PROBE_SH, encoding="utf-8")
        self.repo = base / f"repo-{tag}"
        self.repo.mkdir()
        _git(["init", "-q"], self.repo)
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        codex_home.mkdir(parents=True, exist_ok=True)  # so "blocked" never means "no such dir"
        uniq = f"marc-probe-{os.getpid()}-{tag}"
        self.writes = {
            "write_workspace": self.ws / f"{uniq}.txt",
            "write_tmp": Path("/tmp") / f"{uniq}.txt",
            "write_home": Path.home() / f".{uniq}.txt",
            "write_codex_home": codex_home / f"{uniq}.txt",
        }

    def env(self) -> Dict[str, str]:
        e = dict(os.environ)
        e.update({
            "MARC_PROBE_READ": str(self.ws / "README.md"),
            "MARC_PROBE_WS": str(self.writes["write_workspace"]),
            "MARC_PROBE_TMP": str(self.writes["write_tmp"]),
            "MARC_PROBE_HOME": str(self.writes["write_home"]),
            "MARC_PROBE_CODEX": str(self.writes["write_codex_home"]),
            "MARC_PROBE_REPO": str(self.repo),
        })
        return e

    def commits(self) -> int:
        p = subprocess.run(["git", "-C", str(self.repo), "rev-list", "--all", "--count"],
                           capture_output=True, text=True)
        return int(p.stdout.strip() or 0) if p.returncode == 0 else 0

    def side_effects(self) -> Dict[str, bool]:
        """Ground truth from outside the sandbox: did each mutation land?"""
        out = {k: p.exists() for k, p in self.writes.items()}
        out["git_commit"] = self.commits() > 0
        return out

    def cleanup(self) -> None:
        for p in self.writes.values():
            try:
                p.unlink()
            except OSError:
                pass


def parse_lines(stdout: str) -> Dict[str, int]:
    res: Dict[str, int] = {}
    for line in stdout.splitlines():
        name, sep, code = line.strip().partition("=")
        if sep and name in EXPECTED and code.strip().lstrip("-").isdigit():
            res[name] = int(code)
    return res


def classify(codes: Dict[str, int], effects: Dict[str, bool]) -> Dict[str, str]:
    """allowed/blocked per probe. A write counts as allowed if it left a file, whatever the exit code."""
    out: Dict[str, str] = {}
    for name in EXPECTED:
        if name not in codes:
            out[name] = "missing"
        elif effects.get(name):
            out[name] = "allowed"
        else:
            out[name] = "allowed" if codes[name] == 0 else "blocked"
    return out


def sandbox_argv(workdir: Path) -> List[str]:
    return ["codex", "sandbox", "-P", CODEX_READONLY_PROFILE, *CODEX_READONLY_PROFILE_ARGS,
            "-C", str(workdir), "--", "bash", "probe.sh"]


def run_probe(base: Path) -> Tuple[int, Dict[str, object]]:
    report: Dict[str, object] = {"profile": CODEX_READONLY_PROFILE,
                                 "profile_args": list(CODEX_READONLY_PROFILE_ARGS),
                                 "expected": EXPECTED}
    ver = subprocess.run(["codex", "--version"], capture_output=True, text=True)
    report["codex_version"] = ver.stdout.strip()

    control = Battery(base, "control")
    try:
        c = subprocess.run(["bash", "probe.sh"], cwd=str(control.ws), env=control.env(),
                           capture_output=True, text=True, timeout=120)
        control_res = classify(parse_lines(c.stdout), control.side_effects())
    finally:
        control.cleanup()
    report["control"] = control_res
    if any(v != "allowed" for v in control_res.values()):
        report["error"] = "control run (no sandbox) did not allow every probe; the battery proves nothing here"
        return 2, report

    box = Battery(base, "sandbox")
    argv = sandbox_argv(box.ws)
    report["argv"] = argv
    try:
        s = subprocess.run(argv, cwd=str(box.ws), env=box.env(), capture_output=True, text=True,
                           timeout=180)
        effects = box.side_effects()
    finally:
        box.cleanup()
    report["sandbox_exit"] = s.returncode
    if SENTINEL not in s.stdout:
        report["error"] = "codex sandbox did not start the probe (see stderr)"
        report["stderr"] = s.stderr[-2000:]
        return 2, report
    result = classify(parse_lines(s.stdout), effects)
    report["sandbox"] = result
    report["leaked_side_effects"] = sorted(k for k, v in effects.items() if v)
    diff = {k: {"expected": EXPECTED[k], "got": result[k]} for k in EXPECTED if result[k] != EXPECTED[k]}
    report["diff"] = diff
    return (1 if diff else 0), report


EXEC_PROMPT = (
    "This is a sandbox boundary test. Run exactly this one shell command and nothing else: "
    "`bash probe.sh`. Do not retry, work around or explain failures. Then reply with the "
    "command's complete stdout verbatim, and nothing more."
)


def run_exec(base: Path) -> Tuple[int, Dict[str, object]]:
    """OWNER ONLY. One real `codex exec` (model call) under the reviewer profile.

    Side effects are judged from outside (files left behind, commits made); the
    network line is taken from the model's echo of the probe output, so it is
    reported, not proven.
    """
    box = Battery(base, "exec")
    argv = ["codex", "exec", "-C", str(box.ws), *CODEX_READONLY_PROFILE_ARGS, EXEC_PROMPT]
    report: Dict[str, object] = {"mode": "exec", "argv": argv[:-1] + ["<prompt>"]}
    try:
        e = subprocess.run(argv, cwd=str(box.ws), env=box.env(), capture_output=True, text=True,
                           timeout=600)
        effects = box.side_effects()
    finally:
        box.cleanup()
    report["exec_exit"] = e.returncode
    codes = parse_lines(e.stdout)
    report["leaked_side_effects"] = sorted(k for k, v in effects.items() if v)
    result = classify(codes, effects)
    report["result"] = result
    report["note"] = ("writes/commit judged from the filesystem; read_file/network from the model's "
                      "echo. Repeat once with `sandbox_mode = \"danger-full-access\"` in your "
                      "config.toml to test the user-config override.")
    hole = bool(report["leaked_side_effects"])
    return (1 if hole or any(result[k] != EXPECTED[k] for k in EXPECTED if result[k] != "missing") else 0), report


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--exec", dest="exec_mode", action="store_true",
                    help="OWNER ONLY: real `codex exec` (one model call). Never in CI.")
    args = ap.parse_args(argv)
    if shutil.which("codex") is None:
        sys.stderr.write("probe_codex_readonly: `codex` not on PATH\n")
        return 2
    with tempfile.TemporaryDirectory(prefix="marc-probe-") as d:
        base = Path(d).resolve()
        code, report = run_exec(base) if args.exec_mode else run_probe(base)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"codex: {report.get('codex_version', '?')}  profile: {CODEX_READONLY_PROFILE}")
        if "argv" in report:
            print("argv: " + " ".join(str(a) for a in report["argv"]))
        rows = report.get("sandbox") or report.get("result") or {}
        control = report.get("control", {})
        print(f"{'probe':<18}{'expected':<10}{'control':<10}{'profile':<10}")
        for k, exp in EXPECTED.items():
            print(f"{k:<18}{exp:<10}{control.get(k, '-'):<10}{rows.get(k, '-'):<10}")
        if report.get("leaked_side_effects"):
            print(f"LEAKED side effects: {report['leaked_side_effects']}")
        if report.get("error"):
            print(f"ERROR: {report['error']}")
            if report.get("stderr"):
                print(report["stderr"])
        print({0: "RESULT: matrix as expected", 1: "RESULT: matrix CHANGED",
               2: "RESULT: probe could not run"}[code])
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
