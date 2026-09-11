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
    
    file_to_check = None
    
    if tool in ('View', 'view_file', 'ViewSource', 'ReadFile'):
        limit = inputs.get('limit') or inputs.get('EndLine')
        offset = inputs.get('offset') or inputs.get('StartLine') or inputs.get('ContentOffset')
        if limit is not None or offset is not None:
            sys.exit(0)
            
        file_to_check = inputs.get('path') or inputs.get('AbsolutePath') or inputs.get('file_path')
        
    elif tool in ('Bash', 'run_command', 'RunCommand'):
        command = inputs.get('command') or inputs.get('CommandLine') or ''
        if '|' in command or '>' in command:
            sys.exit(0)
            
        m = re.match(r'^\s*(cat|less|more|head|tail)\s+([^\s|><]+)\s*$', command)
        if m:
            file_to_check = m.group(2)
            
    if file_to_check and os.path.isfile(file_to_check):
        try:
            with open(file_to_check, 'r', encoding='utf-8', errors='ignore') as f:
                lines = sum(1 for _ in f)
                
            if lines > 350:
                print(json.dumps({
                    'hookSpecificOutput': {
                        'permissionDecision': 'deny',
                        'permissionDecisionReason': f'File exceeds threshold ({lines} > 350 lines). Use targeted reads (limit/offset) or grep.'
                    }
                }))
                sys.exit(0)
        except Exception:
            pass

if __name__ == '__main__':
    main()
"
