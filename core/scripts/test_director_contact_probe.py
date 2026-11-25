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
        self.assertEqual(d["schema"], 3)
        self.assertIsNone(d["harnesses"]["codex"]["app_server"])
        self.assertIsNone(d["multiplexer"]["sessions"])
        self.assertIn("rediscover", d)
        self.assertIn("display name", d["claude_address_hint"])
        self.assertTrue(all(not h["on_path"] and h["version"] is None for h in d["harnesses"].values()))
        self.assertIsNone(d["claude_cross_session"])
        self.assertIsNone(d["host_harness"])
        self.assertIsNone(d["multiplexer"]["active"])
        self.assertEqual(d["adapters"]["codex"], ["exec-resume", "inbox"])
        self.assertEqual(d["adapters"]["claude-code"], ["resume", "inbox"])
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
        self.assertIn("unverified", d["adapter_help"]["send-message"])

    def test_send_message_needs_claude_on_path(self):
        fake(self.bin, "claude", "echo 2.2.0")
        self.assertEqual(self.data()["adapters"]["claude-code"][0], "send-message")
        (self.bin / "claude").unlink()
        self.assertNotIn("send-message", self.data()["adapters"]["claude-code"])

    def test_user_bin_never_executed(self):
        lb = self.cwd / ".local" / "bin"
        lb.mkdir(parents=True)
        marker = self.cwd / "ran"
        fake(lb, "claude", f'touch {marker}; echo 9.9.9')
        fake(lb, "tmux", f'touch {marker}; echo s')
        d = self.data("--list-sessions")
        self.assertFalse(marker.exists())
        h = d["harnesses"]["claude-code"]
        self.assertEqual((h["on_path"], h["version"], h["found_in_user_bin"]), (False, None, True))
        self.assertNotIn("send-message", d["adapters"]["claude-code"])
        self.assertEqual(d["multiplexer"]["sessions"]["tmux"], [])

    def test_probe_timeout_bounds(self):
        fake(self.bin, "codex", "exec /bin/sleep 30")
        for bad in ("0", "-5", "nan", "inf", "abc", "61"):
            r = subprocess.run([sys.executable, "-c",
                "import os,sys;sys.path.insert(0,%r);import director_contact_probe as p;print(p.probe_timeout())"
                % str(PROBE.parent)], env={"MARC_PROBE_TIMEOUT": bad, "PATH": str(self.bin)},
                capture_output=True, text=True)
            self.assertEqual(r.stdout.strip(), "10.0", bad)

    def test_multiplexer_markers_and_no_pane_value_leak(self):
        secret = "PANE-SECRET-77"
        fake(self.bin, "claude", "echo 2.2.0")
        cases = [("tmux", {"TMUX": "/tmp/x,1,0", "TMUX_PANE": secret}),
                 ("zellij", {"ZELLIJ": "0", "ZELLIJ_PANE_ID": secret}),
                 ("zellij", {"ZELLIJ_SESSION_NAME": "s", "ZELLIJ_PANE_ID": secret}),
                 ("screen", {"STY": "1.pts", "WINDOW": secret}),
                 ("kitty", {"KITTY_WINDOW_ID": secret}),
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
            self.assertEqual(d["adapters"]["codex"], ["multiplexer-interactive", "exec-resume", "inbox"])
            self.assertEqual(d["adapters"]["claude-code"][:2], ["send-message", "multiplexer-interactive"])
            (self.bin / mux).unlink()

    def test_no_multiplexer_means_no_adapter(self):
        d = self.data()
        self.assertNotIn("multiplexer-interactive", d["adapters"]["copilot"])
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
        d = self.data(env={"MARC_PROBE_TIMEOUT": "1"})
        self.assertTrue(d["harnesses"]["codex"]["on_path"])
        self.assertIsNone(d["harnesses"]["codex"]["version"])
        self.assertTrue(d["harnesses"]["copilot"]["on_path"])
        self.assertIsNone(d["harnesses"]["copilot"]["version"])

    def test_local_bin_fallback(self):
        lb = self.cwd / ".local" / "bin"
        lb.mkdir(parents=True)
        fake(lb, "tmux", "exit 0")
        d = self.data()
        self.assertIn("tmux", d["multiplexer"]["available"])
        self.assertEqual(d["adapters"]["codex"][0], "multiplexer-interactive")
        (lb / "tmux").chmod(0o644)
        self.assertNotIn("tmux", self.data()["multiplexer"]["available"])

    def test_list_sessions(self):
        fake(self.bin, "tmux", 'echo alpha; echo beta')
        fake(self.bin, "zellij", 'printf "\\033[1mgamma\\033[0m\\n"')
        d = self.data("--list-sessions")
        self.assertEqual(d["multiplexer"]["sessions"]["tmux"], ["alpha", "beta"])
        self.assertEqual(d["multiplexer"]["sessions"]["zellij"], ["gamma"])
        self.assertIsNone(self.data()["multiplexer"]["sessions"])

    def test_list_sessions_failure(self):
        fake(self.bin, "tmux", "exit 1")
        d = self.data("--list-sessions")
        self.assertEqual(d["multiplexer"]["sessions"]["tmux"], [])
        self.assertEqual(d["multiplexer"]["sessions"]["zellij"], [])
        self.assertIn("tmux", d["multiplexer"]["sessions"]["error"])

    def test_list_sessions_none_running_is_empty(self):
        fake(self.bin, "tmux", 'echo "no server running on /tmp/tmux-1/default" >&2; exit 1')
        fake(self.bin, "zellij", 'echo "No active zellij sessions found." >&2; exit 1')
        d = self.data("--list-sessions")
        self.assertEqual(d["multiplexer"]["sessions"], {"tmux": [], "zellij": []})

    def test_list_sessions_tmux_connect_errors(self):
        fake(self.bin, "tmux",
             'echo "error connecting to /tmp/tmux-1/default (Permission denied)" >&2; exit 1')
        d = self.data("--list-sessions")
        self.assertEqual(d["multiplexer"]["sessions"]["tmux"], [])
        self.assertIn("tmux: exit 1", d["multiplexer"]["sessions"]["error"])
        fake(self.bin, "tmux",
             'echo "error connecting to /tmp/tmux-1/default (No such file or directory)" >&2; exit 1')
        d = self.data("--list-sessions")
        self.assertEqual(d["multiplexer"]["sessions"], {"tmux": [], "zellij": []})

    def fake_codex(self, server, top):
        fake(self.bin, "codex",
             'case "$*" in "app-server --help") ' + server + ' ;; "--help") ' + top +
             ' ;; *) echo "codex 9.9.9" ;; esac')

    def test_codex_app_server_capability(self):
        self.fake_codex('echo "      --listen <URL>"', 'echo "      --remote <ADDR>"')
        d = self.data()
        self.assertIs(d["harnesses"]["codex"]["app_server"], True)
        self.assertEqual(d["adapters"]["codex"], ["app-server-steer", "exec-resume", "inbox"])
        self.assertEqual(d["teams"], [])
        self.assertIn("expectedTurnId", d["adapter_help"]["app-server-steer"])
        self.assertIn("MESSAGE-ONLY", d["adapter_help"]["app-server-steer"])
        self.assertIn("--remote-auth-token-env", d["adapter_help"]["app-server-steer"])
        self.assertEqual(self.data("--team", "a=codex")["teams"][0]["adapters"][0], "app-server-steer")
        fake(self.bin, "tmux", "exit 0")
        self.assertEqual(self.data()["adapters"]["codex"],
                         ["app-server-steer", "multiplexer-interactive", "exec-resume", "inbox"])
        (self.bin / "tmux").unlink()
        self.fake_codex('echo "--listen <URL>"', 'echo "no remote here"')
        d = self.data()
        self.assertIs(d["harnesses"]["codex"]["app_server"], False)
        self.assertEqual(d["adapters"]["codex"], ["exec-resume", "inbox"])
        self.fake_codex('echo "no transports"', 'echo "--remote <ADDR>"')
        self.assertIs(self.data()["harnesses"]["codex"]["app_server"], False)

    def test_codex_app_server_unknown_on_failure(self):
        self.fake_codex("exit 2", 'echo "--remote <ADDR>"')
        d = self.data()
        self.assertIsNone(d["harnesses"]["codex"]["app_server"])
        self.assertEqual(d["adapters"]["codex"], ["exec-resume", "inbox"])
        self.fake_codex('echo "--listen <URL>"', "exec /bin/sleep 30")
        d = self.data(env={"MARC_PROBE_TIMEOUT": "1"})
        self.assertIsNone(d["harnesses"]["codex"]["app_server"])

    def test_codex_second_help_skipped_when_first_fails(self):
        marker = self.cwd / "second-ran"
        self.fake_codex("exit 2", f'touch {marker}; echo "--remote <ADDR>"')
        d = self.data()
        self.assertIsNone(d["harnesses"]["codex"]["app_server"])
        self.assertFalse(marker.exists())

    def test_codex_user_bin_never_probed_for_app_server(self):
        lb = self.cwd / ".local" / "bin"
        lb.mkdir(parents=True)
        marker = self.cwd / "ran"
        fake(lb, "codex", f'touch {marker}; echo "--listen --remote"')
        d = self.data()
        self.assertFalse(marker.exists())
        self.assertIsNone(d["harnesses"]["codex"]["app_server"])
        self.assertEqual(d["adapters"]["codex"], ["exec-resume", "inbox"])

    def test_no_writes(self):
        fake(self.bin, "claude", 'echo "2.1.230"')
        fake(self.bin, "tmux", "echo s")
        self.fake_codex('echo "--listen"', 'echo "--remote"')
        r = self.run_probe("--team", "a=claude-code", "--list-sessions",
                       env={"TMUX": "1", "TMUX_PANE": "%1"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(list(self.cwd.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
