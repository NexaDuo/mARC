#!/usr/bin/env python3
"""Tests for director_contact_probe.py (issue #433): real script, fake CLIs."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

PROBE = Path(__file__).resolve().parent / "director_contact_probe.py"


def fake(bindir, name, body):
    p = Path(bindir) / name
    p.write_text("#!/bin/sh\n" + body + "\n")
    p.chmod(0o755)


class ProbeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.bin = Path(self._tmp.name) / "bin"
        self.bin.mkdir()
        self.cwd = Path(self._tmp.name) / "cwd"
        self.cwd.mkdir()

    def run_probe(self, *args, env=None):
        e = {"PATH": str(self.bin), "HOME": str(self.cwd)}
        e.update(env or {})
        return subprocess.run([sys.executable, str(PROBE), *args], cwd=self.cwd, env=e,
                              capture_output=True, text=True, timeout=60)

    def data(self, *args, env=None):
        r = self.run_probe(*args, env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_no_harness(self):
        d = self.data()
        self.assertEqual(d["schema"], 1)
        self.assertTrue(all(not h["on_path"] and h["version"] is None for h in d["harnesses"].values()))
        self.assertIsNone(d["claude_cross_session"])
        self.assertIsNone(d["host_harness"])
        self.assertIsNone(d["multiplexer"]["active"])
        self.assertEqual(d["adapters"]["codex"], ["exec-resume", "inbox"])
        self.assertEqual(d["adapters"]["claude-code"], ["send-message", "resume", "inbox"])
        self.assertEqual(d["teams"], [])
        self.assertIs(d["persist"]["written"], False)
        self.assertIn("inbox", d["adapter_help"])

    def test_each_harness_present(self):
        for name, cli in [("claude-code", "claude"), ("codex", "codex"),
                          ("copilot", "copilot"), ("antigravity", "agy")]:
            fake(self.bin, cli, f'echo "{cli} 9.9.9"')
        d = self.data()
        for name, cli in [("claude-code", "claude"), ("codex", "codex"),
                          ("copilot", "copilot"), ("antigravity", "agy")]:
            self.assertTrue(d["harnesses"][name]["on_path"])
            self.assertEqual(d["harnesses"][name]["version"], f"{cli} 9.9.9")

    def test_claude_version_gate(self):
        fake(self.bin, "claude", 'echo "2.1.223 (Claude Code)"')
        d = self.data()
        self.assertIs(d["claude_cross_session"], False)
        self.assertNotIn("send-message", d["adapters"]["claude-code"])
        fake(self.bin, "claude", 'echo "2.1.224 (Claude Code)"')
        d = self.data()
        self.assertIs(d["claude_cross_session"], True)
        self.assertEqual(d["adapters"]["claude-code"][0], "send-message")
        fake(self.bin, "claude", 'echo "2.2.0 (Claude Code)"')
        self.assertIs(self.data()["claude_cross_session"], True)

    def test_unparsable_version_is_unknown(self):
        fake(self.bin, "claude", 'echo "dev build"')
        d = self.data()
        self.assertIsNone(d["claude_cross_session"])
        self.assertIn("send-message", d["adapters"]["claude-code"])

    def test_multiplexer_markers_and_no_pane_value_leak(self):
        secret = "PANE-SECRET-77"
        cases = [("tmux", {"TMUX": "/tmp/x,1,0", "TMUX_PANE": secret}),
                 ("zellij", {"ZELLIJ": "0", "ZELLIJ_PANE_ID": secret}),
                 ("wezterm", {"WEZTERM_PANE": secret})]
        for mux, env in cases:
            fake(self.bin, mux, "exit 0")
            r = self.run_probe(env=env)
            self.assertEqual(r.returncode, 0)
            self.assertNotIn(secret, r.stdout + r.stderr)
            d = json.loads(r.stdout)
            self.assertEqual(d["multiplexer"]["active"], mux)
            self.assertIn(mux, d["multiplexer"]["available"])
            self.assertIs(d["multiplexer"]["pane_id_env_present"], True)
            self.assertEqual(d["adapters"]["codex"][-1], "multiplexer")
            (self.bin / mux).unlink()

    def test_no_multiplexer_means_no_adapter(self):
        d = self.data()
        self.assertNotIn("multiplexer", d["adapters"]["copilot"])
        self.assertIs(d["multiplexer"]["pane_id_env_present"], False)

    def test_host_harness(self):
        self.assertEqual(self.data(env={"CLAUDECODE": "1"})["host_harness"], "claude-code")
        self.assertEqual(self.data(env={"CODEX_THREAD_ID": "x"})["host_harness"], "codex")

    def test_team_binding_and_bad_harness(self):
        d = self.data("--team", "alpha=codex", "--team", "beta=antigravity", "--json")
        self.assertEqual(d["teams"][0], {"name": "alpha", "harness": "codex",
                                         "adapters": ["exec-resume", "inbox"]})
        self.assertEqual(d["teams"][1]["adapters"], ["conversation-resume", "inbox"])
        for bad in ("alpha=bogus", "alpha", "=codex"):
            r = self.run_probe("--team", bad)
            self.assertEqual(r.returncode, 2)
            self.assertIn("error", r.stderr)

    def test_failing_or_hanging_cli(self):
        fake(self.bin, "codex", "exit 3")
        fake(self.bin, "copilot", "exec /bin/sleep 30")
        d = self.data()
        self.assertTrue(d["harnesses"]["codex"]["on_path"])
        self.assertIsNone(d["harnesses"]["codex"]["version"])
        self.assertTrue(d["harnesses"]["copilot"]["on_path"])
        self.assertIsNone(d["harnesses"]["copilot"]["version"])

    def test_no_writes(self):
        fake(self.bin, "claude", 'echo "2.1.230"')
        self.run_probe("--team", "a=claude-code", env={"TMUX": "1", "TMUX_PANE": "%1"})
        self.assertEqual(list(self.cwd.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
