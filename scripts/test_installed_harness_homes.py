#!/usr/bin/env python3
"""Adapter HOME-isolation self-test for scripts/test_installed_harness.py (#477).

Deterministic, offline, no CLI. Proves that Copilot.hook_run never writes to
(or reads from) the caller's real HOME when --plugin/--source leave
ctx["HOME"] pointing outside the temp dir, and that Codex.hook_run does not
leave CODEX_HOME behind in ctx.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import test_installed_harness as t  # noqa: E402


def snapshot(d):
    return sorted(os.path.join(r, f) for r, ds, fs in os.walk(d) for f in ds + fs)


class Homes(unittest.TestCase):
    def setUp(self):
        self._saved = (t.sh, t.shutil.copytree)

    def tearDown(self):
        t.sh, _ = self._saved

    def test_copilot_never_touches_outside_home(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as fake_home, \
                tempfile.TemporaryDirectory() as plugin:
            os.makedirs(os.path.join(plugin, "scripts"))
            open(os.path.join(plugin, "plugin.json"), "w").write("{}")
            seen = {}

            def fake_sh(argv, **kw):
                seen.update(kw["env"])
                return subprocess.CompletedProcess(argv, 0, "", "")

            t.sh = fake_sh
            ad = t.Copilot(tmp)
            ad.ctx = {"HOME": fake_home}
            ad.hook_run(plugin, tmp, "sessionStart", "*", "true", "")
            self.assertEqual(snapshot(fake_home), [], "wrote into the outside HOME")
            self.assertTrue(seen["HOME"].startswith(os.path.realpath(tmp)) or seen["HOME"].startswith(tmp))
            self.assertTrue(os.path.isdir(os.path.join(seen["HOME"], ".copilot", "installed-plugins", "nexaduo", "marc")))
            self.assertTrue(ad.plugin_data().startswith(tmp))

    def test_codex_does_not_mutate_ctx(self):
        with tempfile.TemporaryDirectory() as tmp:
            t.sh = lambda argv, **kw: subprocess.CompletedProcess(argv, 0, "", "")
            ad = t.Codex(tmp)
            ad.ctx = {"HOME": tmp}
            ad.hook_run("/p", tmp, "SessionStart", "*", "true", "")
            self.assertNotIn("CODEX_HOME", ad.ctx)


if __name__ == "__main__":
    unittest.main()
