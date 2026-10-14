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
        `codex exec` (three model calls) with the reviewer argv, once per case:
        no config; `sandbox_mode = "danger-full-access"` in a user config.toml
        inside a throwaway CODEX_HOME (the real one is never written; see
        run_exec_case for the refresh-token caveat); the same line in a project
        `.codex/config.toml` inside the probe workspace. Never run in CI.

What the default mode proves, and what it does not (#351 SEC-1): it checks what
the profile CONTAINS, with the profile named explicitly by `codex sandbox -P`.
The reviewer argv selects it only through `-c default_permissions=...`; whether
`codex exec` honors that selection over a user or project `sandbox_mode` is
unverified until the owner's `--exec` run passes in all three cases.

Exit codes: 0 matrix as expected; 1 matrix changed (a Codex upgrade changed the
profile semantics, or the probe found a hole); 2 the probe could not run (codex
missing, sandbox failed to start, control run not clean; in --exec mode also a
failed exec, an absent probe sentinel or any missing row).
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
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
        # The checkout is a git repo, as in a real dispatch (`-C <checkout>`);
        # `codex exec` refuses a non-repo dir without --skip-git-repo-check (#351 CDX-1).
        _git(["init", "-q"], self.ws)
        self.repo = base / f"repo-{tag}"
        self.repo.mkdir()
        _git(["init", "-q"], self.repo)
        codex_home = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")
        codex_home.mkdir(parents=True, exist_ok=True)  # so "blocked" never means "no such dir"
        uniq = f"marc-probe-{os.getpid()}-{tag}"
        self.uniq = uniq
        self.writes = {
            "write_workspace": self.ws / f"{uniq}.txt",
            "write_tmp": Path("/tmp") / f"{uniq}.txt",
            "write_home": Path.home() / f".{uniq}.txt",
            "write_codex_home": codex_home / f"{uniq}.txt",
        }

    def retarget_codex_home(self, home: Path) -> None:
        """Point the write_codex_home probe at the CODEX_HOME actually in use (#351 CDX-2)."""
        self.codex_home_override = home
        self.writes["write_codex_home"] = home / f"{self.uniq}.txt"

    def env(self) -> Dict[str, str]:
        e = dict(os.environ)
        override = getattr(self, "codex_home_override", None)
        if override is not None:
            e["CODEX_HOME"] = str(override)
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


OVERRIDE_LINE = 'sandbox_mode = "danger-full-access"'
EXEC_CASES = ("no-config", "user-config", "project-config")


def judge_exec(exit_code: int, stdout: str, effects: Dict[str, bool]) -> Tuple[int, Dict[str, object]]:
    """Fail closed (#351 REV-1): 2 unless exec succeeded, the probe ran and every row is present."""
    result = classify(parse_lines(stdout), effects)
    leaked = sorted(k for k, v in effects.items() if v)
    out: Dict[str, object] = {"exec_exit": exit_code, "sentinel": SENTINEL in stdout,
                              "result": result, "leaked_side_effects": leaked}
    if leaked or any(result[k] not in (EXPECTED[k], "missing") for k in EXPECTED):
        return 1, out  # a proven hole outranks "could not run"
    if exit_code != 0 or SENTINEL not in stdout or "missing" in result.values():
        out["error"] = "codex exec failed or the probe did not run completely; nothing is proven"
        return 2, out
    return 0, out


TEMP_HOME_PREFIX = "marc-probe-codex-home-"
STALE_AFTER_SEC = 3600  # older than any live run (3 cases x 600 s timeout)


def _temp_home_parent() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    return Path(runtime) if runtime and Path(runtime).is_dir() else Path.home() / ".cache"


def sweep_stale_temp_homes(parent: "Path | None" = None, now: "float | None" = None) -> List[str]:
    """Remove temp CODEX_HOMEs a SIGKILLed run left behind (#351 REV-10).

    Only real directories (lstat, never a symlink) owned by this user, named
    with TEMP_HOME_PREFIX and older than STALE_AFTER_SEC. Returns removed names.
    """
    base = parent or _temp_home_parent()
    removed: List[str] = []
    try:
        entries = list(os.scandir(str(base)))
    except OSError:
        return removed
    cutoff = (now if now is not None else time.time()) - STALE_AFTER_SEC
    for entry in entries:
        if not entry.name.startswith(TEMP_HOME_PREFIX):
            continue
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mtime > cutoff:
            continue
        shutil.rmtree(entry.path, ignore_errors=True)
        removed.append(entry.name)
    return removed


def _temp_codex_home() -> Path:
    """A private (0700) throwaway CODEX_HOME for the user-config case (#351 SEC-4).

    Lives under $XDG_RUNTIME_DIR (or ~/.cache), not /tmp. Holds a 0600 copy of
    the auth file and a config.toml carrying OVERRIDE_LINE. The owner's real
    $CODEX_HOME is never written.
    """
    parent = _temp_home_parent()
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    home = Path(tempfile.mkdtemp(prefix=TEMP_HOME_PREFIX, dir=str(parent)))
    try:
        os.chmod(str(home), 0o700)
        real_auth = _real_codex_home() / "auth.json"
        if real_auth.is_file():
            fd = os.open(str(home / "auth.json"), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(real_auth.read_bytes())
        (home / "config.toml").write_text(OVERRIDE_LINE + "\n", encoding="utf-8")
    except BaseException:
        shutil.rmtree(str(home), ignore_errors=True)
        raise
    return home


def _real_codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex")


class _Terminated(Exception):
    pass


def _raise_terminated(signum: int, _frame: object) -> None:
    raise _Terminated(f"signal {signum}")


def run_exec_case(base: Path, case: str) -> Tuple[int, Dict[str, object]]:
    """One real `codex exec` (one model call) under the reviewer profile, for one config case.

    no-config       the user's config as is
    user-config     CODEX_HOME points (for that subprocess only) at a throwaway
                    0700 dir holding a 0600 auth copy and a config.toml with
                    OVERRIDE_LINE; it is removed afterwards, also on SIGTERM/SIGHUP.
                    The real $CODEX_HOME/config.toml is never touched (SEC-4).
                    The write_codex_home probe targets that temp home, the one
                    actually in use. Caveats: if Codex rotates the refresh token
                    during this run, the rotation lands in the temp copy and the
                    real auth.json can go stale (a re-login fixes it). The temp
                    home carries no other user settings, so an owner who needs a
                    custom provider/model config gets exit 2 (could not run) for
                    this case: fail closed, never a false pass.
    project-config  OVERRIDE_LINE in <workspace>/.codex/config.toml (the reviewed checkout)
    Writes and the commit are judged from the filesystem; read_file and network
    come from the model's echo of the probe output.
    """
    box = Battery(base, f"exec-{case}")
    argv = ["codex", "exec", "-C", str(box.ws), *CODEX_READONLY_PROFILE_ARGS, EXEC_PROMPT]
    temp_home: "Path | None" = None
    old = {sig: signal.signal(sig, _raise_terminated) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        if case == "user-config":
            temp_home = _temp_codex_home()
            # The active CODEX_HOME is the temp one: probe writes into it (CDX-2).
            box.retarget_codex_home(temp_home)
        env = box.env()
        if case == "project-config":
            (box.ws / ".codex").mkdir()
            (box.ws / ".codex" / "config.toml").write_text(OVERRIDE_LINE + "\n", encoding="utf-8")
        try:
            e = subprocess.run(argv, cwd=str(box.ws), env=env, capture_output=True, text=True,
                               timeout=600)
            code_out = (e.returncode, e.stdout, e.stderr)
        except (OSError, subprocess.SubprocessError) as exc:
            code_out = (127, "", str(exc))
        effects = box.side_effects()
    finally:
        if temp_home is not None:
            shutil.rmtree(str(temp_home), ignore_errors=True)
        box.cleanup()
        for sig, handler in old.items():
            signal.signal(sig, handler)
    code, out = judge_exec(code_out[0], code_out[1], effects)
    if code == 2:
        out["stderr"] = code_out[2][-1000:]
    return code, out


def run_exec(base: Path) -> Tuple[int, Dict[str, object]]:
    """OWNER ONLY: every EXEC_CASES case. Exit 1 if any case leaked, else 2 if any could not run."""
    report: Dict[str, object] = {"mode": "exec",
                                 "argv": ["codex", "exec", "-C", "<ws>", *CODEX_READONLY_PROFILE_ARGS, "<prompt>"]}
    report["swept_stale_temp_homes"] = sweep_stale_temp_homes()
    cases: Dict[str, object] = {}
    codes = []
    for case in EXEC_CASES:
        code, out = run_exec_case(base, case)
        out["code"] = code
        cases[case] = out
        codes.append(code)
    report["cases"] = cases
    return (1 if 1 in codes else 2 if 2 in codes else 0), report


def main(argv: List[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--json", action="store_true", help="print the full report as JSON")
    ap.add_argument("--exec", dest="exec_mode", action="store_true",
                    help="OWNER ONLY: real `codex exec`, three model calls (no config, user "
                         "config override, project config override). Never in CI.")
    args = ap.parse_args(argv)
    if shutil.which("codex") is None:
        sys.stderr.write("probe_codex_readonly: `codex` not on PATH\n")
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="marc-probe-") as d:
            base = Path(d).resolve()
            code, report = run_exec(base) if args.exec_mode else run_probe(base)
    except _Terminated as e:
        # Interrupted (SIGTERM/SIGHUP): could not run, never "matrix changed" (REV-8).
        sys.stderr.write(f"probe_codex_readonly: interrupted by {e}; nothing is proven\n")
        return 2
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"codex: {report.get('codex_version', '?')}  profile: {CODEX_READONLY_PROFILE}")
        if "argv" in report:
            print("argv: " + " ".join(str(a) for a in report["argv"]))
        if "cases" in report:
            cases = report["cases"]
            print(f"{'probe':<18}{'expected':<10}" + "".join(f"{c:<16}" for c in cases))
            for k, exp in EXPECTED.items():
                print(f"{k:<18}{exp:<10}" + "".join(f"{v['result'].get(k, '-'):<16}" for v in cases.values()))
            for c, v in cases.items():
                print(f"{c}: exit={v['exec_exit']} sentinel={v['sentinel']} code={v['code']}"
                      + (f" ERROR: {v['error']}" if v.get("error") else ""))
                if v.get("leaked_side_effects"):
                    print(f"{c}: LEAKED side effects: {v['leaked_side_effects']}")
        else:
            rows = report.get("sandbox") or {}
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
