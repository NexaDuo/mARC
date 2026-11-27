#!/usr/bin/env python3
"""Rejection path of the run_hooks trust guard (#172): a tampered hooks.json
command, or a tampered hook script, is a recorded FAIL and is NOT executed.
Deterministic, no harness CLI: copies the checkout's claude-code output."""
import os
import shutil
import sys
import tempfile
import json
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_installed_harness as t  # noqa: E402

SRC = os.path.join(t.ROOT, "harnesses", "claude-code", "marc")


class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="marc-guard-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.plugin = os.path.join(self.tmp, "plugin")
        shutil.copytree(SRC, self.plugin, symlinks=True)
        self.marker = os.path.join(self.tmp, "PWNED")
        t.failures.clear()
        t.xfails.clear()
        t.HARNESS = "claude-code"

    def run_it(self):
        ad = t.ClaudeCode(os.path.join(self.tmp, "run"))
        os.makedirs(ad.tmp)
        t.run_hooks(ad, self.plugin, "")

    def test_untampered_runs_clean(self):
        self.run_it()
        self.assertEqual(t.failures, [])

    def test_tampered_command_fails_and_does_not_run(self):
        p = os.path.join(self.plugin, "hooks", "hooks.json")
        d = json.load(open(p, encoding="utf-8"))
        for entries in d["hooks"].values():
            for e in entries:
                for h in e["hooks"]:
                    h["command"] += f"; touch {self.marker}"
        json.dump(d, open(p, "w", encoding="utf-8"))
        self.run_it()
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(any("match the checkout" in f for f in t.failures))

    def test_command_pin_alone_rejects(self):
        """Content check passes (files identical) but the command list differs:
        the byte-for-byte command pin must still refuse to run it."""
        ad = t.ClaudeCode(os.path.join(self.tmp, "run"))
        os.makedirs(ad.tmp)
        real = t.ClaudeCode.hooks

        def tampered(self_, plugin):
            for label, ev, m, c in real(self_, plugin):
                yield label, ev, m, (c + f"; touch {self.marker}") if plugin != SRC else c
        t.ClaudeCode.hooks = tampered
        self.addCleanup(setattr, t.ClaudeCode, "hooks", real)
        t.run_hooks(ad, self.plugin, "")
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(any("matches the compiled" in f for f in t.failures))

    def test_tampered_script_fails_and_does_not_run(self):
        script = os.path.join(self.plugin, "hooks", "invariants-card.sh")
        with open(script, "a", encoding="utf-8") as f:
            f.write(f"\ntouch {self.marker}\n")
        self.run_it()
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(any("match the checkout" in f for f in t.failures))


if __name__ == "__main__":
    unittest.main()
