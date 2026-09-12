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

    tool = payload.get('toolName') or payload.get('tool') or payload.get('name') or ''
    inputs = payload.get('toolInput') or payload.get('input') or payload.get('arguments') or {}
    
    file_to_check = None
    
    if tool in ('View', 'view_file', 'ViewSource', 'ReadFile'):
        limit = inputs.get('limit') or inputs.get('EndLine')
        offset = inputs.get('offset') or inputs.get('StartLine') or inputs.get('ContentOffset')
        if limit is not None or offset is not None:
            sys.exit(0)
            
        file_to_check = inputs.get('path') or inputs.get('AbsolutePath') or inputs.get('file_path')
        
    elif tool in ('Bash', 'run_command', 'RunCommand'):
        command = inputs.get('command') or inputs.get('CommandLine') or ''
        if any(c in command for c in ['|', '>', '<', '&', ';']):
            sys.exit(0)
            
        import shlex
        try:
            parts = shlex.split(command)
            if parts and parts[0] in ('cat', 'less', 'more', 'head', 'tail') and len(parts) == 2:
                file_to_check = parts[1]
        except Exception:
            pass
            
    if file_to_check and os.path.isfile(file_to_check):
        try:
            max_lines = 350
            for toml_path in ('.agents/team.toml', '.claude/team.toml'):
                if os.path.isfile(toml_path):
                    try:
                        with open(toml_path, 'r', encoding='utf-8') as tf:
                            in_token_guard = False
                            for line in tf:
                                line = line.split('#')[0].strip()
                                if not line:
                                    continue
                                if line.startswith('[') and line.endswith(']'):
                                    in_token_guard = (line == '[token_guard]')
                                elif in_token_guard and line.startswith('max_read_lines'):
                                    parts = line.split('=')
                                    if len(parts) == 2:
                                        try:
                                            max_lines = int(parts[1].strip())
                                        except ValueError:
                                            pass
                    except Exception:
                        pass
                        
            lines = 0
            with open(file_to_check, 'r', encoding='utf-8', errors='ignore') as f:
                for _ in f:
                    lines += 1
                    if lines > max_lines:
                        break
                        
            if lines > max_lines:
                print(json.dumps({
                    'hookSpecificOutput': {
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
