#!/usr/bin/env bash
# opt-in read-guard script for PreToolUse to intercept large file reads (Issue #262)
# Bulk-reader delegation (opt-in, off by default) added in issue #320: when
# [token_guard].bulk_reader = true, an over-threshold untargeted read is
# routed to core/scripts/dispatch_agent.py --harness antigravity, which
# writes a summary to disk; the guard then denies the original read with a
# path to that (small, cheap-to-read) summary instead of bare advice.

set -u

# Resolve this script's own directory so the embedded Python (run via
# `python3 -c`, where `__file__` is unavailable) can find dispatch_agent.py
# next to it without guessing at a relative/absolute cwd (issue #320).
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export MARC_READ_GUARD_DIR="$SCRIPT_DIR"

python3 -c "
import sys
import json
import os
import re
import subprocess
import tempfile
import time

def main():
    try:
        payload_str = sys.stdin.read()
        if not payload_str.strip():
            sys.exit(0)
        payload = json.loads(payload_str)
    except Exception:
        sys.exit(0)

    # Recursion guard (issue #320): the bulk-reader worker runs on a
    # different harness/CLI process but inherits this process's environment
    # (dispatch_agent.py's subprocess.run does not override env). If that
    # worker's own summarization prompt triggers an untargeted read of a
    # large file, its own PreToolUse read-guard (if configured) would try to
    # delegate again. This marker, set only around the delegated subprocess
    # call below, makes any nested invocation of this same script exit
    # immediately instead of recursing.
    if os.environ.get('MARC_BULK_READER_ACTIVE') == '1':
        sys.exit(0)

    try:
        out = subprocess.check_output(['ps', '-o', 'args=', '-s', str(os.getsid(0))], text=True)
        if re.search(r'(--agent\s+(sec|rev)|/marc:(sec|rev))', out):
            sys.exit(0)
    except Exception:
        pass

    # 'tool_name'/'tool_input' are Claude Code's actual PreToolUse stdin field
    # names (snake_case, confirmed at docs.claude.com/en/docs/claude-code/hooks
    # and /tools-reference — 'Read' is the exact built-in file-read tool name).
    # The camelCase/'name'/'arguments' fallbacks are kept for any other
    # harness that ships this hook with a differently-cased payload, but they
    # must not shadow the real Claude Code field (issue #287: the prior
    # toolName/tool/name-only lookup never matched 'tool_name', so this guard
    # silently matched nothing on the only harness it now ships on).
    tool = payload.get('tool_name') or payload.get('toolName') or payload.get('tool') or payload.get('name') or ''
    inputs = payload.get('tool_input') or payload.get('toolInput') or payload.get('input') or payload.get('arguments') or {}

    files_to_check = []

    if tool in ('Read', 'View', 'view_file', 'ViewSource', 'ReadFile'):
        limit = inputs.get('limit') or inputs.get('EndLine')
        offset = inputs.get('offset') or inputs.get('StartLine') or inputs.get('ContentOffset')
        if limit is not None or offset is not None:
            sys.exit(0)

        path = inputs.get('path') or inputs.get('AbsolutePath') or inputs.get('file_path')
        if path:
            files_to_check.append(path)

    elif tool in ('Bash', 'run_command', 'RunCommand'):
        import shlex
        command = inputs.get('command') or inputs.get('CommandLine') or ''
        for m in re.finditer(r'\b(?:cat|less|more|head|tail)\s+([^&;|><]+)', command):
            args_str = m.group(1)
            try:
                for arg in shlex.split(args_str):
                    if not arg.startswith('-'):
                        files_to_check.append(arg)
            except Exception:
                pass

    valid_files = [f for f in files_to_check if f and os.path.isfile(f)]

    if valid_files:
        try:
            # Genuinely opt-in (issue #287): the guard stays inert (no
            # enforcement at all) unless the repo's team.toml declares a
            # [token_guard] section. #262 titled this feature 'opt-in', and
            # #287 found it was about to become active-by-default for every
            # consuming repo the moment it got wired into a harness's
            # hook_ids — a silent behaviour change nobody consented to.
            # 'max_lines' only takes its 350 default once a repo has already
            # opted in by adding an (empty or partial) [token_guard] section;
            # with no section at all, 'section_found' stays False and the
            # function returns before ever comparing line counts.
            #
            # 'bulk_reader'/'bulk_reader_timeout' (issue #320) are a SEPARATE
            # opt-in from 'section_found' itself: denying a read is local,
            # dispatching a subprocess against an external CLI is not, so a
            # repo that already opted into the deny-only guard must opt in
            # again, explicitly, to have it spawn 'agy'. Both default off/safe.
            section_found = False
            max_lines = 350
            bulk_reader_enabled = False
            bulk_reader_timeout = 20
            for toml_path in ('.agents/team.toml', '.claude/team.toml'):
                if os.path.isfile(toml_path):
                    try:
                        try:
                            import tomllib
                            with open(toml_path, 'rb') as tf:
                                data = tomllib.load(tf)
                                if 'token_guard' in data:
                                    section_found = True
                                    tg = data['token_guard']
                                    if 'max_read_lines' in tg:
                                        max_lines = int(tg['max_read_lines'])
                                    if 'bulk_reader' in tg:
                                        bulk_reader_enabled = bool(tg['bulk_reader'])
                                    if 'bulk_reader_timeout' in tg:
                                        bulk_reader_timeout = int(tg['bulk_reader_timeout'])
                        except ImportError:
                            with open(toml_path, 'r', encoding='utf-8') as tf:
                                in_token_guard = False
                                for line in tf:
                                    line = line.split('#')[0].strip()
                                    if not line:
                                        continue
                                    if line.startswith('[') and line.endswith(']'):
                                        in_token_guard = (line == '[token_guard]')
                                        if in_token_guard:
                                            section_found = True
                                    elif in_token_guard and '=' in line:
                                        key, _, val = line.partition('=')
                                        key = key.strip()
                                        val = val.split('#')[0].strip()
                                        if key == 'max_read_lines':
                                            try:
                                                max_lines = int(val)
                                            except ValueError:
                                                pass
                                        elif key == 'bulk_reader':
                                            bulk_reader_enabled = val.strip().lower() == 'true'
                                        elif key == 'bulk_reader_timeout':
                                            try:
                                                bulk_reader_timeout = int(val)
                                            except ValueError:
                                                pass
                    except Exception:
                        pass

            if not section_found:
                sys.exit(0)

            for file_to_check in valid_files:
                lines = 0
                with open(file_to_check, 'r', encoding='utf-8', errors='ignore') as f:
                    for _ in f:
                        lines += 1
                        if lines > max_lines:
                            break

                if lines > max_lines:
                    reason = f'File exceeds threshold ({lines} > {max_lines} lines). Use targeted reads (limit/offset) or grep.'

                    if bulk_reader_enabled:
                        summary_path = try_bulk_reader(file_to_check, bulk_reader_timeout)
                        if summary_path:
                            reason = (
                                f'File exceeds threshold ({lines} > {max_lines} lines). '
                                f'A bulk-reader summary was generated on a cheaper worker: '
                                f'read {summary_path} instead (it is well under the threshold).'
                            )

                    print(json.dumps({
                        'hookSpecificOutput': {
                            'hookEventName': 'PreToolUse',
                            'permissionDecision': 'deny',
                            'permissionDecisionReason': reason
                        }
                    }))
                    sys.exit(0)
        except Exception:
            pass


def try_bulk_reader(file_path, timeout_sec):
    '''Delegate summarizing an over-threshold file to a cheap harness (issue #320).

    Fail-open, always: any failure here (missing agy binary, dispatch
    subprocess error, non-zero exit, empty/missing output, timeout) returns
    None so the caller falls back to the pre-existing bare deny-with-advice.
    Never raises, never hangs the session past timeout_sec + a small buffer
    for the outer subprocess itself.
    '''
    try:
        import shutil as _shutil
        # Checked directly here (not left to dispatch_agent.py's own
        # fallback) so a missing 'agy' binary short-circuits before ever
        # spawning a subprocess: dispatch_agent.py's fallback-to-host-harness
        # would otherwise resolve to 'claude' inside a Claude Code hook,
        # which is a different, unwanted failure mode (a nested claude CLI
        # invocation from within this hook) rather than the fail-open this
        # feature promises.
        if _shutil.which('agy') is None:
            return None

        here = os.environ.get('MARC_READ_GUARD_DIR') or os.path.dirname(os.path.abspath(sys.argv[0]))
        dispatch_script = os.path.join(here, 'dispatch_agent.py')
        if not os.path.isfile(dispatch_script):
            return None

        scratch_dir = os.path.join(tempfile.gettempdir(), 'marc-bulk-reader')
        os.makedirs(scratch_dir, exist_ok=True)
        base = os.path.basename(file_path)
        summary_path = os.path.join(
            scratch_dir,
            f'{base}.{os.getpid()}.{int(time.time() * 1000)}.summary.md',
        )

        prompt = (
            f'Read the file at {file_path!r} in full. Write a concise summary '
            f'(purpose, key functions/classes, notable logic) to disk at '
            f'{summary_path!r} as plain text, well under 350 lines. '
            f'Do not modify {file_path!r}. When done, output only the word DONE.'
        )

        env = dict(os.environ)
        env['MARC_BULK_READER_ACTIVE'] = '1'

        proc = subprocess.run(
            [
                sys.executable or 'python3', dispatch_script,
                '--role', 'research',
                '--prompt', prompt,
                '--harness', 'antigravity',
                '--timeout', str(timeout_sec),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 5,
            env=env,
        )
        if proc.returncode != 0:
            return None
        if not os.path.isfile(summary_path):
            return None
        if os.path.getsize(summary_path) == 0:
            return None
        return summary_path
    except Exception:
        return None


if __name__ == '__main__':
    main()
"
