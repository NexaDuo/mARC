#!/usr/bin/env python3
"""Fail-closed tests for probe_codex_readonly.py --exec (origin: #351 · 2026-10-14).

A stub `codex` stands in for the real CLI, so no model is ever called. HOME and
CODEX_HOME point at a temp dir: the user-config case edits only that copy.
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


def run_with_stub(stub_body: str) -> "tuple[subprocess.CompletedProcess[str], Path]":
    tmp = Path(tempfile.mkdtemp(prefix="marc-probe-test-"))
    bin_dir = tmp / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "codex"
    stub.write_text("#!/bin/sh\n" + stub_body + "\n", encoding="utf-8")
    stub.chmod(0o755)
    home = tmp / "home"
    (home / ".codex").mkdir(parents=True)
    (home / ".codex" / "config.toml").write_text('model = "x"\n', encoding="utf-8")
    env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
               HOME=str(home), CODEX_HOME=str(home / ".codex"))
    proc = subprocess.run([sys.executable, str(PROBE), "--exec"], env=env, capture_output=True,
                          text=True, timeout=120)
    return proc, home


class ExecFailsClosed(unittest.TestCase):
    def test_failing_codex_exits_nonzero(self):
        proc, home = run_with_stub("exit 1")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertNotIn("matrix as expected", proc.stdout)
        for case in probe.EXEC_CASES:
            self.assertIn(f"{case}: exit=1", proc.stdout)
        # The user-config case restored the user's config byte for byte.
        self.assertEqual((home / ".codex" / "config.toml").read_text(), 'model = "x"\n')

    def test_silent_success_is_not_a_pass(self):
        # exec "succeeds" but the probe never ran: no sentinel, every row missing.
        proc, _ = run_with_stub("exit 0")
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

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

    def test_user_override_line(self):
        self.assertEqual(probe._with_user_override('model = "x"\n'),
                         'sandbox_mode = "danger-full-access"\nmodel = "x"\n')
        self.assertEqual(probe._with_user_override('sandbox_mode = "read-only"\n[t]\nk = 1\n'),
                         'sandbox_mode = "danger-full-access"\n[t]\nk = 1\n')


if __name__ == "__main__":
    unittest.main()
