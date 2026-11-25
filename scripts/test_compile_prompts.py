#!/usr/bin/env python3
"""Tests for scripts/compile_prompts.py argument handling and --check (#417)."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "compile_prompts.py")
ROOT = os.path.dirname(os.path.dirname(SCRIPT))


def _mini_repo(tmp):
    """A minimal repo (scripts/compile_prompts.py + core/ + one harness)."""
    os.makedirs(os.path.join(tmp, "scripts"))
    shutil.copy(SCRIPT, os.path.join(tmp, "scripts", "compile_prompts.py"))
    os.makedirs(os.path.join(tmp, "core", "agents"))
    with open(os.path.join(tmp, "core", "agents", "x.md"), "w") as f:
        f.write("hello {{ name }}\n")
    marc = os.path.join(tmp, "harnesses", "h", "marc")
    os.makedirs(marc)
    with open(os.path.join(marc, "compile.json"), "w") as f:
        f.write('{"name": "world", "plugin_root_env": "ROOT"}\n')
    return os.path.join(tmp, "scripts", "compile_prompts.py"), marc


def _snapshot(root):
    out = {}
    for d, _, files in os.walk(root):
        for n in files:
            p = os.path.join(d, n)
            with open(p, "rb") as f:
                out[os.path.relpath(p, root)] = f.read()
    return out


def run(script, *args):
    return subprocess.run([sys.executable, script, *args], capture_output=True, text=True)


class CompilePromptsCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.script, self.marc = _mini_repo(self.tmp)

    def test_help_exits_zero_and_writes_nothing(self):
        before = _snapshot(self.tmp)
        r = run(self.script, "--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--check", r.stdout)
        self.assertEqual(before, _snapshot(self.tmp))

    def test_unknown_arg_exits_two_and_writes_nothing(self):
        before = _snapshot(self.tmp)
        r = run(self.script, "--bogus")
        self.assertEqual(r.returncode, 2)
        self.assertEqual(before, _snapshot(self.tmp))

    def test_check_reports_drift_without_writing_then_passes_after_compile(self):
        before = _snapshot(self.tmp)
        r = run(self.script, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("agents/x.md", r.stderr)
        self.assertEqual(before, _snapshot(self.tmp))
        self.assertEqual(run(self.script).returncode, 0)
        with open(os.path.join(self.marc, "agents", "x.md")) as f:
            self.assertEqual(f.read(), "hello world\n")
        self.assertEqual(run(self.script, "--check").returncode, 0)

    def test_check_flags_hand_edit_and_missing_file(self):
        run(self.script)
        with open(os.path.join(self.marc, "agents", "x.md"), "a") as f:
            f.write("edited\n")
        os.makedirs(os.path.join(self.tmp, "core", "scripts"))
        with open(os.path.join(self.tmp, "core", "scripts", "s.sh"), "w") as f:
            f.write("echo\n")
        before = _snapshot(self.tmp)
        r = run(self.script, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("x.md (differs)", r.stderr)
        self.assertIn("scripts/s.sh (missing)", r.stderr)
        self.assertEqual(before, _snapshot(self.tmp))


class RealTree(unittest.TestCase):
    def test_check_passes_on_committed_tree(self):
        r = run(SCRIPT, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
