#!/usr/bin/env python3
"""Fail-closed tests for probe_codex_readonly.py --exec (origin: #351 · 2026-10-14).

A stub `codex` stands in for the real CLI, so no model is ever called. HOME,
CODEX_HOME and XDG_RUNTIME_DIR point at temp dirs; the tests assert the "real"
CODEX_HOME is never modified and the throwaway one is removed (SEC-4).
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
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
    # `codex [-c k=v] features list`: loaded only if a trust override was passed AND the
    # cwd project config enables the control flag (STUB_FEATURES=never simulates no load).
    'case " $* " in *" features list "*)\n'
    '  echo "features $*" >> "$STUB_LOG"\n'
    '  v=false\n'
    # Loaded only if the override names the EXACT workspace path (REV-2).
    '  case "$*" in *\'{"\'"$(pwd -P)"\'"={trust_level="trusted"}}\'*) grep -q "gcp_setup_wizard = true" .codex/config.toml 2>/dev/null'
    ' && [ "${STUB_FEATURES:-}" != never ] && v=true;; esac\n'
    '  echo "gcp_setup_wizard                         stable             $v"; exit 0;;\n'
    "esac\n"
    'echo "execargs=$*" >> "$STUB_LOG"\n'
    'echo "home=$CODEX_HOME" >> "$STUB_LOG"\n'
    'cat "$CODEX_HOME/config.toml" >> "$STUB_LOG" 2>/dev/null; echo >> "$STUB_LOG"\n'
    'if [ -f "$CODEX_HOME/auth.json" ]; then '
    'echo "authmode=$(stat -c %a "$CODEX_HOME/auth.json")" >> "$STUB_LOG"; fi\n'
    # argv is `codex exec -C <ws> ...`: record whether <ws> is a git work tree (CDX-1)
    # and where the write_codex_home probe points (CDX-2).
    'ws=""; prev=""; for a in "$@"; do [ "$prev" = -C ] && ws=$a; prev=$a; done\n'
    'echo "cwd_git=$(git -C "$ws" rev-parse --is-inside-work-tree 2>/dev/null || echo no)" >> "$STUB_LOG"\n'
    'echo "probe_codex=$MARC_PROBE_CODEX" >> "$STUB_LOG"\n'
)


_TMPS: "list[Path]" = []


def tearDownModule():
    for d in _TMPS:
        shutil.rmtree(str(d), ignore_errors=True)


def run_with_stub(stub_body: str, extra_env: "dict | None" = None) -> "tuple[subprocess.CompletedProcess[str], Path]":
    tmp = Path(tempfile.mkdtemp(prefix="marc-probe-test-"))
    _TMPS.append(tmp)
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
               STUB_LOG=str(tmp / "stub.log"), **(extra_env or {}))
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
        self.addCleanup(shutil.rmtree, str(parent), True)
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

    def test_trust_args_shape(self):
        # #358: inline-table form, one -c pair, nothing written to any config file.
        ws = Path("/tmp/a b/ws")
        self.assertEqual(probe.trust_args(ws),
                         ["-c", 'projects={"/tmp/a b/ws"={trust_level="trusted"}}'])

    def test_only_project_config_is_trusted_and_controlled(self):
        proc, tmp = run_with_stub("exit 1")
        log = (tmp / "stub.log").read_text(errors="replace").splitlines()
        execs = [ln for ln in log if ln.startswith("execargs=")]
        self.assertEqual(len(execs), len(probe.EXEC_CASES))
        trusted = [ln for ln in execs if "trust_level" in ln]
        self.assertEqual(len(trusted), 1)
        self.assertIn("-C ", trusted[0])
        # REV-1: trust comes BEFORE `exec`, the same position the control uses.
        self.assertTrue(trusted[0].startswith("execargs=-c projects="), trusted[0])
        self.assertIn(" exec -C ", trusted[0])
        # REV-4: the printed argv carries the trust args.
        self.assertIn("project-config: argv: codex -c projects=", proc.stdout)
        # REV-3: both control calls happen before the project-config exec.
        seq = [ln for ln in log if ln.startswith(("features ", "execargs="))]
        k = next(i for i, ln in enumerate(seq) if "trust_level" in ln and ln.startswith("execargs="))
        self.assertEqual([ln.split()[0] for ln in seq[k - 2:k]], ["features", "features"])
        self.assertIn("/ws-exec-project-config", trusted[0])
        # control: one untrusted + one trusted `features list`, both before that exec.
        feats = [ln for ln in log if ln.startswith("features ")]
        self.assertEqual(len(feats), 2)
        self.assertNotIn("trust_level", feats[0])
        self.assertIn("trust_level", feats[1])
        self.assertIn("positive control", proc.stdout)
        self.assertIn(probe.CONTROL_FEATURE, probe.PROJECT_CONFIG)
        self.assertIn(probe.OVERRIDE_LINE, probe.PROJECT_CONFIG)

    def test_failed_positive_control_skips_exec(self):
        proc, tmp = run_with_stub("exit 0", {"STUB_FEATURES": "never"})
        log = (tmp / "stub.log").read_text(errors="replace").splitlines()
        execs = [ln for ln in log if ln.startswith("execargs=")]
        self.assertEqual(len(execs), len(probe.EXEC_CASES) - 1)  # project-config never ran
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("positive control failed", proc.stdout)

    def test_wrong_path_trust_is_not_loaded(self):
        # REV-2: trust naming another path must not count as loaded.
        ws = Path(tempfile.mkdtemp(prefix="marc-probe-test-"))
        _TMPS.append(ws)
        (ws / ".codex").mkdir()
        (ws / ".codex" / "config.toml").write_text(probe.PROJECT_CONFIG)
        bin_dir = ws / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "codex"
        stub.write_text(STUB_HEAD + "exit 1\n")
        stub.chmod(0o755)
        env = dict(os.environ, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                   STUB_LOG=str(ws / "log"))
        good = probe.positive_control(ws.resolve(), env)
        self.assertTrue(good["loaded"], good)
        real = probe.trust_args
        probe.trust_args = lambda _ws: real(Path("/elsewhere"))
        try:
            bad = probe.positive_control(ws.resolve(), env)
        finally:
            probe.trust_args = real
        self.assertFalse(bad["loaded"], bad)

    def test_real_config_change_fails_the_case(self):
        # SEC-3: codex touching the real config.toml during project-config is exit 2.
        proc, tmp = run_with_stub('echo x >> "$CODEX_HOME/config.toml"; exit 1')
        self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
        self.assertIn("project-config: exit=1", proc.stdout)
        self.assertIn("config.toml changed", proc.stdout)

    def test_trust_args_rejects_unencodable_path(self):
        with self.assertRaises(ValueError):
            probe.trust_args(Path("/tmp/a\x7fb"))
        with self.assertRaises(UnicodeEncodeError):
            probe.trust_args(Path("/tmp/\udc80"))
        self.assertIn("é", probe.trust_args(Path("/tmp/é"))[1])  # not \u-escaped

    def test_deferred_signals_restore_the_saved_mask_and_cover_sigint(self):
        # SEC-1/SEC-2: SIGINT is held too; the prior mask is restored, not blanket-unblocked.
        signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGUSR1})
        self.addCleanup(signal.pthread_sigmask, signal.SIG_UNBLOCK, {signal.SIGUSR1})
        with probe._deferred_signals():
            held = signal.pthread_sigmask(signal.SIG_BLOCK, set())
            self.assertTrue({signal.SIGTERM, signal.SIGHUP, signal.SIGINT} <= held)
        after = signal.pthread_sigmask(signal.SIG_BLOCK, set())
        self.assertIn(signal.SIGUSR1, after)
        self.assertNotIn(signal.SIGINT, after)
        old = signal.signal(signal.SIGINT, signal.default_int_handler)
        self.addCleanup(signal.signal, signal.SIGINT, old)
        with self.assertRaises(KeyboardInterrupt):
            with probe._deferred_signals():
                os.kill(os.getpid(), signal.SIGINT)

    def test_feature_state_parse(self):
        self.assertIs(probe.feature_state("x stable true\ngcp_setup_wizard  stable  true\n"), True)
        self.assertIs(probe.feature_state("gcp_setup_wizard stable false\n"), False)
        self.assertIsNone(probe.feature_state("nothing here\n"))

    def test_deferred_signals_hold_until_block_ends(self):
        # #358: a SIGTERM that lands during cleanup waits; it fires only after the block.
        def handler(signum, _frame):
            raise probe._Terminated(f"signal {signum}")
        old = signal.signal(signal.SIGTERM, handler)
        self.addCleanup(signal.signal, signal.SIGTERM, old)
        done = []
        with self.assertRaises(probe._Terminated):
            with probe._deferred_signals():
                os.kill(os.getpid(), signal.SIGTERM)
                done.append("cleanup finished")
        self.assertEqual(done, ["cleanup finished"])

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
