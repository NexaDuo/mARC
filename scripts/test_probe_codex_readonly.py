#!/usr/bin/env python3
"""Fail-closed tests for probe_codex_readonly.py --exec (origin: #351 · 2026-10-14).

A stub `codex` stands in for the real CLI, so no model is ever called. HOME,
CODEX_HOME and XDG_RUNTIME_DIR point at temp dirs; the tests assert the "real"
CODEX_HOME is never modified and the throwaway one is removed (SEC-4).
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
PROBE = HERE / "probe_codex_readonly.py"
sys.path.insert(0, str(HERE))
import probe_codex_readonly as probe  # noqa: E402

REAL_CONFIG = b'model = "x"\r\n\xff\n'  # CRLF + non-UTF-8: must survive byte for byte
REAL_AUTH = b'{"token": "stub"}'

# The stub records the CODEX_HOME it saw, that home's config and the auth file
# mode, then runs the test's body.
STUB_HEAD = (
    "#!/bin/sh\n"
    'echo "home=$CODEX_HOME" >> "$STUB_LOG"\n'
    'cat "$CODEX_HOME/config.toml" >> "$STUB_LOG" 2>/dev/null; echo >> "$STUB_LOG"\n'
    'if [ -f "$CODEX_HOME/auth.json" ]; then '
    'echo "authmode=$(stat -c %a "$CODEX_HOME/auth.json")" >> "$STUB_LOG"; fi\n'
    # argv is `codex exec -C <ws> ...`: record whether <ws> is a git work tree (CDX-1)
    # and where the write_codex_home probe points (CDX-2).
    'echo "cwd_git=$(git -C "$3" rev-parse --is-inside-work-tree 2>/dev/null || echo no)" >> "$STUB_LOG"\n'
    'echo "probe_codex=$MARC_PROBE_CODEX" >> "$STUB_LOG"\n'
)


def run_with_stub(stub_body: str) -> "tuple[subprocess.CompletedProcess[str], Path]":
    tmp = Path(tempfile.mkdtemp(prefix="marc-probe-test-"))
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text(STUB_HEAD + stub_body + "\n", encoding="utf-8")
    stub.chmod(0o755)
    real = tmp / "home" / ".codex"
    real.mkdir(parents=True)
    (real / "config.toml").write_bytes(REAL_CONFIG)
    (real / "auth.json").write_bytes(REAL_AUTH)
    runtime = tmp / "runtime"
    runtime.mkdir(mode=0o700)
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               HOME=str(tmp / "home"), CODEX_HOME=str(real), XDG_RUNTIME_DIR=str(runtime),
               STUB_LOG=str(tmp / "stub.log"))
    proc = subprocess.run([sys.executable, str(PROBE), "--exec"], env=env, capture_output=True,
                          text=True, timeout=120)
    return proc, tmp


class ExecFailsClosed(unittest.TestCase):
    def _assert_real_home_untouched(self, tmp: Path) -> None:
        real = tmp / "home" / ".codex"
        self.assertEqual((real / "config.toml").read_bytes(), REAL_CONFIG)
        self.assertEqual((real / "auth.json").read_bytes(), REAL_AUTH)
        self.assertEqual(sorted(p.name for p in real.iterdir()), ["auth.json", "config.toml"])
        log = (tmp / "stub.log").read_text(errors="replace")
        homes = [ln.split("=", 1)[1] for ln in log.splitlines() if ln.startswith("home=")]
        self.assertEqual(len(homes), len(probe.EXEC_CASES), log)
        temp = [h for h in homes if h != str(real)]
        self.assertEqual(len(temp), 1, log)  # only the user-config case swaps CODEX_HOME
        self.assertTrue(temp[0].startswith(str(tmp / "runtime") + os.sep), temp[0])
        self.assertFalse(Path(temp[0]).exists(), "temp CODEX_HOME must be removed")
        self.assertEqual(list((tmp / "runtime").iterdir()), [])
        self.assertIn(probe.OVERRIDE_LINE, log)
        self.assertIn("authmode=600", log)

    def test_failing_codex_exits_nonzero(self):
        proc, tmp = run_with_stub("exit 1")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn("matrix as expected", proc.stdout)
        for case in probe.EXEC_CASES:
            self.assertIn(f"{case}: exit=1", proc.stdout)
        self._assert_real_home_untouched(tmp)

    def test_silent_success_is_not_a_pass(self):
        # exec "succeeds" but the probe never ran: no sentinel, every row missing.
        proc, tmp = run_with_stub("exit 0")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self._assert_real_home_untouched(tmp)

    def test_exec_cwd_is_a_git_repo(self):
        # CDX-1: `codex exec -C <ws>` refuses a non-repo dir; every case must run in one.
        _proc, tmp = run_with_stub("exit 1")
        lines = [ln for ln in (tmp / "stub.log").read_text(errors="replace").splitlines()
                 if ln.startswith("cwd_git=")]
        self.assertEqual(lines, ["cwd_git=true"] * len(probe.EXEC_CASES))

    def test_codex_home_probe_targets_active_home(self):
        # CDX-2: in the user-config case the write_codex_home target is inside the
        # temp CODEX_HOME in use, and a write there is caught before cleanup.
        proc, tmp = run_with_stub('echo x > "$MARC_PROBE_CODEX"; exit 1')
        log = (tmp / "stub.log").read_text(errors="replace").splitlines()
        homes = [ln.split("=", 1)[1] for ln in log if ln.startswith("home=")]
        targets = [ln.split("=", 1)[1] for ln in log if ln.startswith("probe_codex=")]
        for home, target in zip(homes, targets):
            self.assertEqual(str(Path(target).parent), home)
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)  # the leak is reported
        self.assertIn("user-config: LEAKED side effects: ['write_codex_home']", proc.stdout)
        self.assertEqual(list((tmp / "runtime").iterdir()), [])

    def test_sigterm_exits_2_and_cleans_up(self):
        # REV-8: an interrupted run is "could not run" (2), never "matrix changed" (1).
        proc, tmp = run_with_stub('kill -TERM "$PPID"; sleep 5; exit 1')
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("interrupted", proc.stderr)
        self.assertEqual(list((tmp / "runtime").iterdir()), [])
        self.assertEqual((tmp / "home" / ".codex" / "config.toml").read_bytes(), REAL_CONFIG)

    def test_sweep_stale_temp_homes(self):
        # REV-10: only stale, user-owned real dirs with the prefix go; symlinks are not followed.
        parent = Path(tempfile.mkdtemp(prefix="marc-sweep-test-"))
        stale = parent / (probe.TEMP_HOME_PREFIX + "old")
        fresh = parent / (probe.TEMP_HOME_PREFIX + "new")
        other = parent / "unrelated-old"
        victim = parent / "victim"
        for d in (stale, fresh, other, victim):
            d.mkdir()
        (stale / "auth.json").write_text("x")
        (victim / "keep").write_text("x")
        link = parent / (probe.TEMP_HOME_PREFIX + "link")
        link.symlink_to(victim)
        old = 1_000_000.0
        for d in (stale, other):
            os.utime(d, (old, old))
        os.utime(link, (old, old), follow_symlinks=False)
        removed = probe.sweep_stale_temp_homes(parent, now=old + probe.STALE_AFTER_SEC + 1)
        self.assertEqual(removed, [stale.name])
        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists() and other.exists() and link.is_symlink())
        self.assertTrue((victim / "keep").exists())

    def test_judge_exec(self):
        none = {k: False for k in ("write_workspace", "write_tmp", "write_home",
                                   "write_codex_home", "git_commit")}
        full = "MARC_PROBE_BEGIN\n" + "".join(
            f"{k}={0 if v == 'allowed' else 1}\n" for k, v in probe.EXPECTED.items())
        self.assertEqual(probe.judge_exec(0, full, none)[0], 0)
        self.assertEqual(probe.judge_exec(1, full, none)[0], 2)          # exec failed
        self.assertEqual(probe.judge_exec(0, full.replace("MARC_PROBE_BEGIN\n", ""), none)[0], 2)
        self.assertEqual(probe.judge_exec(0, full.replace("network=0\n", ""), none)[0], 2)  # missing row
        self.assertEqual(probe.judge_exec(0, full, dict(none, write_home=True))[0], 1)    # leak
        self.assertEqual(probe.judge_exec(1, "", dict(none, git_commit=True))[0], 1)      # leak wins


if __name__ == "__main__":
    unittest.main()
