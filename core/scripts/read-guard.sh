#!/usr/bin/env bash
# opt-in read-guard script for PreToolUse to intercept large file reads (Issue #262)

set -u

python3 -c "
import sys
import json
import os
import re
import subprocess

def main():
    try:
        payload_str = sys.stdin.read()
        if not payload_str.strip():
            sys.exit(0)
        payload = json.loads(payload_str)
    except Exception:
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
            section_found = False
            max_lines = 350
            for toml_path in ('.agents/team.toml', '.claude/team.toml'):
                if os.path.isfile(toml_path):
                    try:
                        try:
                            import tomllib
                            with open(toml_path, 'rb') as tf:
                                data = tomllib.load(tf)
                                if 'token_guard' in data:
                                    section_found = True
                                    if 'max_read_lines' in data['token_guard']:
                                        max_lines = int(data['token_guard']['max_read_lines'])
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
                                    elif in_token_guard and line.startswith('max_read_lines'):
                                        parts = line.split('=', 1)
                                        if len(parts) == 2:
                                            try:
                                                max_lines = int(parts[1].split('#')[0].strip())
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
                    print(json.dumps({
                        'hookSpecificOutput': {
                            'hookEventName': 'PreToolUse',
                            'permissionDecision': 'deny',
                            'permissionDecisionReason': f'File exceeds threshold ({lines} > {max_lines} lines). Use targeted reads (limit/offset) or grep.'
                        }
                    }))
                    sys.exit(0)
        except Exception:
            pass

if __name__ == '__main__':
    main()
"
