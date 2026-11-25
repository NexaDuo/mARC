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


    def test_check_flags_lost_exec_bit(self):
        with open(os.path.join(self.tmp, "core", "agents", "x.md"), "w") as f:
            f.write("hello\n")
        os.makedirs(os.path.join(self.tmp, "core", "scripts"))
        s = os.path.join(self.tmp, "core", "scripts", "s.sh")
        with open(s, "w") as f:
            f.write("echo\n")
        os.chmod(s, 0o755)
        run(self.script)
        out = os.path.join(self.marc, "scripts", "s.sh")
        self.assertTrue(os.stat(out).st_mode & 0o111)
        self.assertEqual(run(self.script, "--check").returncode, 0)
        os.chmod(out, 0o644)
        r = run(self.script, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("scripts/s.sh (differs)", r.stderr)

    def test_tree_files_records_symlinked_dir(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        try:
            import compile_prompts
        finally:
            sys.path.pop(0)
        os.symlink(self.tmp, os.path.join(self.marc, "linkdir"))
        entry = compile_prompts._tree_files(self.marc)["linkdir"]
        self.assertEqual(entry, ("link", self.tmp, False))

    def test_check_ignores_gitignored_but_reports_untracked(self):
        os.makedirs(os.path.join(self.tmp, "core", "scripts"))
        with open(os.path.join(self.tmp, "core", "scripts", "s.sh"), "w") as f:
            f.write("echo\n")
        run(self.script)
        git = ["git", "-C", self.tmp]
        subprocess.run(git + ["init", "-q"], check=True)
        with open(os.path.join(self.tmp, ".gitignore"), "w") as f:
            f.write("*.local\n")
        for n in ("a.local", "b.txt"):
            with open(os.path.join(self.marc, "scripts", n), "w") as f:
                f.write("x\n")
        r = run(self.script, "--check")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("a.local", r.stderr)
        self.assertIn("b.txt (stale)", r.stderr)

    def test_git_ignored_empty_without_git(self):
        sys.path.insert(0, os.path.dirname(SCRIPT))
        try:
            import compile_prompts
        finally:
            sys.path.pop(0)
        old = os.environ["PATH"]
        os.environ["PATH"] = self.tmp  # no git binary here
        try:
            self.assertEqual(compile_prompts._git_ignored(self.tmp), set())
        finally:
            os.environ["PATH"] = old

    def test_check_reports_untracked_when_not_a_checkout(self):
        os.makedirs(os.path.join(self.tmp, "core", "scripts"))
        with open(os.path.join(self.tmp, "core", "scripts", "s.sh"), "w") as f:
            f.write("echo\n")
        run(self.script)
        with open(os.path.join(self.marc, "scripts", "x.local"), "w") as f:
            f.write("x\n")
        r = run(self.script, "--check")  # tmp has no .git: nothing is ignored
        self.assertEqual(r.returncode, 1)
        self.assertIn("x.local (stale)", r.stderr)


class RealTree(unittest.TestCase):
    def test_check_passes_on_committed_tree(self):
        r = run(SCRIPT, "--check")
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
