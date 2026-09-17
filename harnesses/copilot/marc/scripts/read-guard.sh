#!/usr/bin/env bash
# opt-in read-guard script for PreToolUse to intercept large file reads (Issue #262)
# Bulk-reader delegation (opt-in, off by default) added in issue #320: when
# [token_guard].bulk_reader = true, an over-threshold untargeted read is
# routed to core/scripts/dispatch_agent.py --role bulk-reader --harness
# claude-code. That role's tools: Read-only frontmatter is a verified,
# harness-enforced hard boundary on claude-code (issue #322/#323 review), so
# the worker has no execution surface even if the file it summarizes tries to
# steer it. The guard itself writes the worker's summary text to disk (the
# worker has no Write tool to do it itself) and denies the original read with
# a path to that (small, cheap-to-read) summary instead of bare advice.

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
    #
    # NOTE (issue #320 review, LOW finding): this is NOT scoped to 'skip the
    # re-entrant read of the same file' -- it disables read-guard
    # enforcement for the ENTIRE delegated worker session, because the
    # marker is set once on the subprocess's environment and inherited by
    # everything that process (and anything it forks) does for its whole
    # lifetime. If the worker is steered off-task (e.g. by the very content
    # it was asked to summarize), it has zero size-guard enforcement on
    # ANY file it reads during that session, not just the original target.
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
                        bulk_result = try_bulk_reader(file_to_check, bulk_reader_timeout, max_lines)
                        if bulk_result:
                            summary_path, summary_truncated = bulk_result
                            if summary_truncated:
                                # issue #322 review (MEDIUM): the worker's own
                                # 350-line instruction is advisory, not
                                # enforced -- a verbose-output injection (the
                                # Read-only sandbox stops it from ACTING, not
                                # from talking a lot) can still blow past
                                # max_lines. Say so honestly instead of
                                # claiming 'well under the threshold' when
                                # that was never actually checked.
                                reason = (
                                    f'File exceeds threshold ({lines} > {max_lines} lines). '
                                    f'A bulk-reader summary was generated on a cheaper worker but '
                                    f'exceeded the {max_lines}-line threshold itself and was truncated '
                                    f'to fit: read {summary_path} instead.'
                                )
                            else:
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


def try_bulk_reader(file_path, timeout_sec, max_lines):
    '''Delegate summarizing an over-threshold file to a cheap, minimal-tool
    worker (issue #320; routed to the dedicated 'bulk-reader' role on
    claude-code per the #322/#323 review -- NOT 'research' on antigravity,
    which was found to give the delegated worker Bash/WebFetch/WebSearch
    with no enforced restriction on the antigravity harness).

    Fail-open, always: any failure here (missing claude binary, dispatch
    subprocess error, non-zero exit, empty output, timeout) returns None so
    the caller falls back to the pre-existing bare deny-with-advice. Never
    raises, never hangs the session past timeout_sec + a small buffer for the
    outer subprocess itself.

    Delivery shape: the worker has NO Write tool (issue #320/#322 review,
    HIGH finding -- Read-only is the whole point of the sandbox), so it
    cannot write its own summary to disk. Instead it is instructed to output
    the summary as its final response text, which THIS function (not the
    worker) captures from the subprocess's stdout and writes to the scratch
    file itself.

    Returns (summary_path, truncated) on success, None on any fail-open path.
    'max_lines' bounds the summary itself (issue #322 review, MEDIUM finding
    -- chosen fix: truncate-and-disclose over verify-then-fallback, so a
    worker that overshoots its own 350-line instruction, whether by an
    honest miscount or a verbose-output injection the Read-only sandbox does
    nothing to stop, still yields a usable capped file instead of throwing
    away a completed delegation and falling back to the bare deny. The
    caller is told plainly when this happened rather than the reason
    asserting an unverified 'well under the threshold').
    '''
    try:
        import shutil as _shutil
        # Checked directly here (not left to dispatch_agent.py's own
        # fallback) so a missing 'claude' binary short-circuits before ever
        # spawning a subprocess, rather than silently falling back to
        # whatever host harness dispatch_agent.py would otherwise pick.
        if _shutil.which('claude') is None:
            return None

        here = os.environ.get('MARC_READ_GUARD_DIR') or os.path.dirname(os.path.abspath(sys.argv[0]))
        dispatch_script = os.path.join(here, 'dispatch_agent.py')
        if not os.path.isfile(dispatch_script):
            return None

        # A fixed, predictable path under shared /tmp (e.g.
        # tempfile.gettempdir()/'marc-bulk-reader' with exist_ok=True) would
        # let a co-resident local user pre-create or symlink that directory
        # and plant/swap content at the summary path this function discloses
        # back to the frontier model. tempfile.mkdtemp() instead creates a
        # brand-new, exclusively-owned (mode 0700), guaranteed-unique
        # directory per invocation -- there is no pre-existing path to have
        # been tampered with, and no other local user can have write access
        # to it (issue #320 review, MEDIUM finding).
        scratch_dir = tempfile.mkdtemp(prefix='marc-bulk-reader-')
        base = os.path.basename(file_path)
        summary_path = os.path.join(scratch_dir, f'{base}.summary.md')

        prompt = (
            f'Read the file at {file_path!r} in full. Then output, as your '
            f'entire final response, a concise factual summary (purpose, key '
            f'functions/classes, notable logic), well under 350 lines. '
            f'Output only the summary text itself -- no preamble, no '
            f'commentary, nothing else.'
        )

        env = dict(os.environ)
        env['MARC_BULK_READER_ACTIVE'] = '1'

        proc = subprocess.run(
            [
                sys.executable or 'python3', dispatch_script,
                '--role', 'bulk-reader',
                '--prompt', prompt,
                '--harness', 'claude-code',
                '--timeout', str(timeout_sec),
            ],
            capture_output=True,
            text=True,
            timeout=timeout_sec + 5,
            env=env,
        )
        if proc.returncode != 0:
            return None
        summary_text = (proc.stdout or '').strip()
        if not summary_text:
            return None

        summary_lines = summary_text.split('\n')
        truncated = False
        if max_lines > 0 and len(summary_lines) > max_lines:
            truncated = True
            summary_lines = summary_lines[:max_lines - 1] if max_lines > 1 else []
            summary_lines.append(
                f'[... truncated: bulk-reader worker output exceeded the '
                f'{max_lines}-line threshold ...]'
            )
            summary_text = '\n'.join(summary_lines)

        with open(summary_path, 'w', encoding='utf-8') as sf:
            sf.write(summary_text)
        return summary_path, truncated
    except Exception:
        return None


if __name__ == '__main__':
    main()
"
