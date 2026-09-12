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
        out = subprocess.check_output(['ps', '-o', 'args=', '-A'], text=True)
        if re.search(r'(--agent\s+(sec|rev)|/marc:(sec|rev))', out):
            sys.exit(0)
    except Exception:
        pass

    tool = payload.get('toolName') or payload.get('tool') or payload.get('name') or ''
    inputs = payload.get('toolInput') or payload.get('input') or payload.get('arguments') or {}
    
    files_to_check = []
    
    if tool in ('View', 'view_file', 'ViewSource', 'ReadFile'):
        limit = inputs.get('limit') or inputs.get('EndLine')
        offset = inputs.get('offset') or inputs.get('StartLine') or inputs.get('ContentOffset')
        if limit is not None or offset is not None:
            sys.exit(0)
            
        path = inputs.get('path') or inputs.get('AbsolutePath') or inputs.get('file_path')
        if path:
            files_to_check.append(path)
        
    elif tool in ('Bash', 'run_command', 'RunCommand'):
        command = inputs.get('command') or inputs.get('CommandLine') or ''
        
        for m in re.finditer(r'\b(?:cat|less|more|head|tail)\s+([^&;|><]+)', command):
            args_str = m.group(1)
            for arg in args_str.split():
                if not arg.startswith('-'):
                    files_to_check.append(arg.strip("'\""))
                    
    valid_files = [f for f in files_to_check if f and os.path.isfile(f)]
    
    if valid_files:
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
                        
            for file_to_check in valid_files:
                with open(file_to_check, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = sum(1 for _ in f)
                    
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
