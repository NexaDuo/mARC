#!/usr/bin/env python3
"""Self-test for `read-guard.sh` (origin: #262, #287, #320).

Stdlib only (no pytest); run directly: python3 test_read_guard.py

Deterministic, offline, zero token cost. Drives the real `read-guard.sh`
(a bash wrapper around an inline `python3 -c` heredoc) as a subprocess,
feeding it synthetic `PreToolUse` JSON payloads on stdin against a scratch
`.agents/team.toml` and scratch target files, exactly as Claude Code would.

Covers the regression matrix from issue #320:
  (a) no [token_guard] section              -> guard fully inert
  (b) [token_guard] without bulk_reader key -> today's deny, unchanged
  (c) bulk_reader enabled, worker succeeds  -> summary path returned
  (d) bulk_reader enabled, worker missing/fails/times out -> plain deny,
      no hang (does NOT depend on `antigravity`/`agy` being installed)
  (e) @sec/@rev bypass still short-circuits
  (f) targeted read (limit/offset) still passes through untouched
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
READ_GUARD_SH = os.path.join(HERE, "read-guard.sh")

_failures: List[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


def write_team_toml(root: str, body: Optional[str]) -> None:
    """Write (or omit) `.agents/team.toml` at `root`. body=None omits the file."""
    agents_dir = os.path.join(root, ".agents")
    os.makedirs(agents_dir, exist_ok=True)
    if body is None:
        path = os.path.join(agents_dir, "team.toml")
        if os.path.isfile(path):
            os.remove(path)
        return
    with open(os.path.join(agents_dir, "team.toml"), "w", encoding="utf-8") as f:
        f.write(body)


def write_big_file(root: str, name: str = "big.py", lines: int = 400) -> str:
    path = os.path.join(root, name)
    with open(path, "w", encoding="utf-8") as f:
        for i in range(lines):
            f.write(f"x_{i} = {i}\n")
    return path


def run_guard(
    cwd: str,
    payload: Dict[str, Any],
    extra_path: Optional[str] = None,
    extra_env: Optional[Dict[str, str]] = None,
    timeout: float = 20.0,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if extra_path:
        env["PATH"] = extra_path + os.pathsep + env.get("PATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", READ_GUARD_SH],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
        timeout=timeout,
    )


def parse_deny_reason(proc: subprocess.CompletedProcess) -> Optional[str]:
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    return data.get("hookSpecificOutput", {}).get("permissionDecisionReason")


def make_fake_claude(bin_dir: str, behavior: str) -> None:
    """Write a fake `claude` executable to `bin_dir` for a given `behavior`.

    Routed to `claude` (not `agy`) since issue #322/#323 review moved the
    bulk-reader worker to the 'bulk-reader' role on the claude-code harness
    (the harness where `tools:` restrictions are measured/enforced), away
    from 'research' on antigravity.

    behavior:
      'success' - prints a fixed summary as its ENTIRE stdout (the worker has
                  no Write tool, issue #320/#322 review HIGH finding -- the
                  guard itself captures this stdout and writes it to disk).
      'fail'    - exits non-zero, writes nothing.
      'hang'    - sleeps far longer than any timeout under test.
      'empty'   - exits 0 but prints nothing (blank stdout).
      'verbose' - exits 0 but prints MORE lines than any max_read_lines used
                  in these tests, simulating a worker that overshoots its own
                  350-line instruction (honest miscount, or a verbose-output
                  injection the Read-only sandbox does nothing to stop) --
                  issue #322 review MEDIUM finding.
    """
    os.makedirs(bin_dir, exist_ok=True)
    claude_path = os.path.join(bin_dir, "claude")
    if behavior == "success":
        script = "#!/usr/bin/env bash\necho 'summary: fake content'\nexit 0\n"
    elif behavior == "fail":
        script = "#!/usr/bin/env bash\nexit 1\n"
    elif behavior == "hang":
        script = "#!/usr/bin/env bash\nsleep 60\n"
    elif behavior == "empty":
        script = "#!/usr/bin/env bash\nexit 0\n"
    elif behavior == "verbose":
        script = "#!/usr/bin/env bash\nfor i in $(seq 1 500); do echo \"line $i\"; done\nexit 0\n"
    else:
        raise ValueError(f"unknown behavior: {behavior}")
    with open(claude_path, "w", encoding="utf-8") as f:
        f.write(script)
    st = os.stat(claude_path)
    os.chmod(claude_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


def test_no_token_guard_section_is_inert() -> None:
    print("\n--- Test (a): no [token_guard] section -> guard fully inert ---")
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, None)
        big = write_big_file(tmp)
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}})
        check(proc.returncode == 0, f"guard exits 0 with no team.toml (got {proc.returncode})")
        check(proc.stdout.strip() == "", f"guard emits no deny JSON with no team.toml (got {proc.stdout!r})")

        # A partial/empty repo directory (no .agents dir at all either) must
        # also stay inert -- this is the exact #287 regression shape.
        write_team_toml(tmp, "[orchestration]\nmode = \"hybrid\"\n")
        proc2 = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}})
        check(proc2.returncode == 0 and proc2.stdout.strip() == "", "team.toml present but no [token_guard] section -> still inert")


def test_token_guard_without_bulk_reader_key_unchanged() -> None:
    print("\n--- Test (b): [token_guard] without bulk_reader key -> today's deny, unchanged ---")
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, "[token_guard]\nmax_read_lines = 350\n")
        big = write_big_file(tmp)
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}})
        reason = parse_deny_reason(proc)
        check(reason is not None, f"guard denies over-threshold read (got stdout={proc.stdout!r})")
        check(reason is not None and "exceeds threshold" in reason, f"deny reason mentions threshold ({reason})")
        check(reason is not None and "bulk-reader" not in reason.lower(), f"deny reason has no bulk-reader mention when key absent ({reason})")


def test_bulk_reader_enabled_worker_succeeds() -> None:
    print("\n--- Test (c): bulk_reader enabled, worker succeeds -> summary path returned ---")
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(
            tmp,
            "[token_guard]\nmax_read_lines = 350\nbulk_reader = true\nbulk_reader_timeout = 5\n",
        )
        big = write_big_file(tmp)
        bin_dir = os.path.join(tmp, "fakebin")
        make_fake_claude(bin_dir, "success")
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir)
        reason = parse_deny_reason(proc)
        check(reason is not None, f"guard still denies the original read (got stdout={proc.stdout!r}, stderr={proc.stderr!r})")
        check(reason is not None and "bulk-reader summary was generated" in reason, f"reason names the delegated summary ({reason})")

        summary_path = None
        if reason:
            for token in reason.split():
                if token.startswith("/") and os.path.isfile(token.rstrip(".,")):
                    summary_path = token.rstrip(".,")
                    break
        check(summary_path is not None, f"a real summary path is embedded in the reason ({reason})")
        scratch_dir = None
        if summary_path:
            with open(summary_path, encoding="utf-8") as f:
                content = f.read()
            check(len(content) > 0, "summary file is non-empty")
            scratch_dir = os.path.dirname(summary_path)
            # issue #320 review (MEDIUM): the scratch directory must be a
            # fresh, exclusively-owned mkdtemp() dir, not the old fixed,
            # world-guessable '<tmp>/marc-bulk-reader' path -- a plant/swap
            # window for a co-resident local user who controls that path.
            check(
                os.path.basename(scratch_dir) != "marc-bulk-reader",
                f"scratch dir is NOT the old fixed/predictable name (got {scratch_dir})",
            )
            check(
                stat.S_IMODE(os.stat(scratch_dir).st_mode) == 0o700,
                f"scratch dir is exclusively owned (mode 0700, got {oct(stat.S_IMODE(os.stat(scratch_dir).st_mode))})",
            )
            try:
                os.remove(summary_path)
            except OSError:
                pass

        # A second invocation must land in a DIFFERENT scratch dir -- proves
        # the dir is genuinely per-invocation (mkdtemp), not a shared
        # fixed/reused path that a second run's summary could collide into.
        proc2 = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir)
        reason2 = parse_deny_reason(proc2)
        summary_path2 = None
        if reason2:
            for token in reason2.split():
                if token.startswith("/") and os.path.isfile(token.rstrip(".,")):
                    summary_path2 = token.rstrip(".,")
                    break
        if summary_path2 and scratch_dir:
            check(
                os.path.dirname(summary_path2) != scratch_dir,
                "a second invocation gets a distinct, freshly-minted scratch dir",
            )
            try:
                os.remove(summary_path2)
            except OSError:
                pass


def test_bulk_reader_fail_open() -> None:
    print("\n--- Test (d): bulk_reader enabled, worker missing/fails/times out -> fall back, bounded, no hang ---")
    toml_body = "[token_guard]\nmax_read_lines = 350\nbulk_reader = true\nbulk_reader_timeout = 3\n"

    # d1. 'claude' missing from PATH entirely -- must NOT depend on any
    # harness binary being pre-installed on the test machine. Use a minimal
    # PATH with none of the fake bins so `shutil.which('claude')` genuinely
    # finds nothing.
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, toml_body)
        big = write_big_file(tmp)
        minimal_path = "/usr/bin:/bin"
        proc = run_guard(
            tmp,
            {"tool_name": "Read", "tool_input": {"path": big}},
            extra_env={"PATH": minimal_path},
        )
        reason = parse_deny_reason(proc)
        check(proc.returncode == 0, f"guard exits 0 even with claude missing (got {proc.returncode})")
        check(reason is not None and "exceeds threshold" in reason, f"falls back to plain deny with claude missing ({reason})")
        check(reason is not None and "bulk-reader" not in reason.lower(), f"no bulk-reader claim when claude is missing ({reason})")

    # d2. 'claude' present but exits non-zero.
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, toml_body)
        big = write_big_file(tmp)
        bin_dir = os.path.join(tmp, "fakebin")
        make_fake_claude(bin_dir, "fail")
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir)
        reason = parse_deny_reason(proc)
        check(reason is not None and "bulk-reader" not in reason.lower(), f"falls back to plain deny when claude exits non-zero ({reason})")

    # d3. 'claude' present but never writes the summary file (empty output).
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, toml_body)
        big = write_big_file(tmp)
        bin_dir = os.path.join(tmp, "fakebin")
        make_fake_claude(bin_dir, "empty")
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir)
        reason = parse_deny_reason(proc)
        check(reason is not None and "bulk-reader" not in reason.lower(), f"falls back to plain deny when claude writes no summary ({reason})")

    # d4. 'claude' hangs well past its configured timeout. The guard must still
    # return within a bounded time (never hang the session).
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, toml_body)
        big = write_big_file(tmp)
        bin_dir = os.path.join(tmp, "fakebin")
        make_fake_claude(bin_dir, "hang")
        import time
        start = time.time()
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir, timeout=30.0)
        elapsed = time.time() - start
        reason = parse_deny_reason(proc)
        check(elapsed < 15.0, f"guard returns well within a bounded time despite a hanging worker (elapsed={elapsed:.1f}s)")
        check(reason is not None and "bulk-reader" not in reason.lower(), f"falls back to plain deny when claude hangs ({reason})")


def test_bulk_reader_truncates_and_discloses_oversized_summary() -> None:
    """Regression test for the #322 review (MEDIUM): a worker summary that
    exceeds max_read_lines must be truncated to fit AND the deny reason must
    say so honestly, never assert 'well under the threshold' when that was
    never actually verified."""
    print("\n--- Test: oversized bulk-reader summary is truncated and honestly disclosed ---")
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(
            tmp,
            "[token_guard]\nmax_read_lines = 350\nbulk_reader = true\nbulk_reader_timeout = 5\n",
        )
        big = write_big_file(tmp)
        bin_dir = os.path.join(tmp, "fakebin")
        make_fake_claude(bin_dir, "verbose")
        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir)
        reason = parse_deny_reason(proc)
        check(reason is not None, f"guard still denies the original read (stdout={proc.stdout!r})")
        check(
            reason is not None and "truncated" in reason.lower(),
            f"reason honestly discloses truncation instead of claiming 'well under the threshold' ({reason})",
        )
        check(
            reason is not None and "well under the threshold" not in reason,
            f"reason does NOT assert the unverified 'well under the threshold' claim ({reason})",
        )

        summary_path = None
        if reason:
            for token in reason.split():
                if token.startswith("/") and os.path.isfile(token.rstrip(".,")):
                    summary_path = token.rstrip(".,")
                    break
        check(summary_path is not None, f"a real (truncated) summary path is embedded in the reason ({reason})")
        if summary_path:
            with open(summary_path, encoding="utf-8") as f:
                content = f.read()
            content_lines = content.split("\n")
            check(
                len(content_lines) <= 350,
                f"truncated summary file itself is capped at max_read_lines (got {len(content_lines)} lines)",
            )
            check(
                "truncated" in content_lines[-1].lower(),
                f"truncated summary file's last line marks the truncation (got {content_lines[-1]!r})",
            )
            try:
                os.remove(summary_path)
            except OSError:
                pass


def test_bulk_reader_routes_to_dedicated_role_on_claude_code() -> None:
    """Regression test for the #322/#323 review: the delegated worker MUST be
    the dedicated 'bulk-reader' role (tools: Read only) on the claude-code
    harness, never 'research' (which carries Bash/WebFetch/WebSearch) and
    never antigravity (where `tools:` restrictions were empirically found
    NOT to be enforced under --dangerously-skip-permissions, issue #323)."""
    print("\n--- Test: bulk-reader worker is dispatched as role='bulk-reader' on harness='claude-code' ---")
    import importlib.util

    dispatch_path = os.path.join(HERE, "dispatch_agent.py")
    spec = importlib.util.spec_from_file_location("dispatch_agent", dispatch_path)
    dispatch_agent = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(dispatch_agent)

    check(
        dispatch_agent.ROLE_TO_AGENT.get("bulk-reader") == "bulk-reader",
        "ROLE_TO_AGENT maps 'bulk-reader' to the dedicated 'bulk-reader' agent (not 'research')",
    )
    for host in ("claude-code", "antigravity", "copilot"):
        check(
            dispatch_agent.DEFAULT_HYBRID_MATRIX.get(host, {}).get("bulk-reader") == "claude-code",
            f"DEFAULT_HYBRID_MATRIX['{host}']['bulk-reader'] == 'claude-code' (must not drift with the host)",
        )

    cmd = dispatch_agent.build_harness_command("claude-code", "bulk-reader", "read this file")
    check(
        cmd == ["claude", "--dangerously-skip-permissions", "--agent", "bulk-reader", "-p", "read this file"],
        f"build_harness_command('claude-code', 'bulk-reader', ...) invokes --agent bulk-reader (got {cmd})",
    )

    # The actual read-guard.sh call path: assert it names role 'bulk-reader'
    # and harness 'claude-code' literally in its subprocess argv, not 'research'/'antigravity'.
    guard_src_path = os.path.join(HERE, "read-guard.sh")
    with open(guard_src_path, encoding="utf-8") as f:
        guard_src = f.read()
    check("'--role', 'bulk-reader'" in guard_src, "read-guard.sh dispatches with --role bulk-reader")
    check("'--harness', 'claude-code'" in guard_src, "read-guard.sh dispatches with --harness claude-code")
    check("'--role', 'research'" not in guard_src, "read-guard.sh no longer dispatches the 'research' role")
    check("'--harness', 'antigravity'" not in guard_src, "read-guard.sh no longer dispatches to the 'antigravity' harness")


def test_sec_rev_bypass_unaffected() -> None:
    print("\n--- Test (e): @sec/@rev bypass still short-circuits ---")
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, "[token_guard]\nmax_read_lines = 350\nbulk_reader = true\n")
        big = write_big_file(tmp)

        # Simulate ps output matching the bypass regex by stubbing `ps` on
        # PATH with a fake that emits an --agent sec/rev command line for
        # this session id, mirroring what the real guard shells out to.
        bin_dir = os.path.join(tmp, "fakebin")
        os.makedirs(bin_dir, exist_ok=True)
        fake_ps = os.path.join(bin_dir, "ps")
        with open(fake_ps, "w", encoding="utf-8") as f:
            f.write("#!/usr/bin/env bash\necho 'claude --agent sec -p do-the-review'\n")
        st = os.stat(fake_ps)
        os.chmod(fake_ps, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)

        proc = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big}}, extra_path=bin_dir)
        check(proc.returncode == 0 and proc.stdout.strip() == "", f"guard is fully inert when running under an @sec/@rev subagent (stdout={proc.stdout!r})")


def test_targeted_read_passes_through() -> None:
    print("\n--- Test (f): targeted read (limit/offset) still passes through untouched ---")
    with tempfile.TemporaryDirectory() as tmp:
        write_team_toml(tmp, "[token_guard]\nmax_read_lines = 350\nbulk_reader = true\n")
        big = write_big_file(tmp)

        proc_limit = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big, "limit": 50}})
        check(proc_limit.returncode == 0 and proc_limit.stdout.strip() == "", "read with 'limit' passes through untouched")

        proc_offset = run_guard(tmp, {"tool_name": "Read", "tool_input": {"path": big, "offset": 10}})
        check(proc_offset.returncode == 0 and proc_offset.stdout.strip() == "", "read with 'offset' passes through untouched")


def main() -> int:
    if not os.path.isfile(READ_GUARD_SH):
        check(False, f"read-guard.sh not found at {READ_GUARD_SH}")
        return 1
    if shutil.which("bash") is None:
        check(False, "bash not found on PATH -- cannot exercise read-guard.sh")
        return 1

    test_no_token_guard_section_is_inert()
    test_token_guard_without_bulk_reader_key_unchanged()
    test_bulk_reader_enabled_worker_succeeds()
    test_bulk_reader_truncates_and_discloses_oversized_summary()
    test_bulk_reader_routes_to_dedicated_role_on_claude_code()
    test_bulk_reader_fail_open()
    test_sec_rev_bypass_unaffected()
    test_targeted_read_passes_through()

    if _failures:
        print(f"\n{len(_failures)} failure(s):")
        for f in _failures:
            print(f"  - {f}")
        return 1

    print("\nread_guard self-test: OK (all test cases passed).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
