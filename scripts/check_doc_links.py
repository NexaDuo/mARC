#!/usr/bin/env python3
"""Doc link check (#452): fail on any broken in-repo link or #anchor.

Checks README.md and docs/*.md: every relative Markdown link must resolve to an
existing path and, when it carries #anchor, to an existing heading. Also checks
docs/index.html and docs/llms.txt: every github.com/NexaDuo/mARC/blob/main/...
URL must point at an existing file and heading. docs/marc/ records are history
and are not scanned. Anchors use GitHub's slug rules.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BLOB = 'https://github.com/NexaDuo/mARC/blob/main/'
MD_LINK = re.compile(r'\[[^\]]*\]\(([^)\s]+)\)')
BLOB_URL = re.compile(re.escape(BLOB) + r'([^\s"\'<>)\]]+)')


def slug(heading):
    text = re.sub(r'[`*_]', '', heading.strip().lower())
    text = re.sub(r'[^\w\- ]', '', text)
    return text.replace(' ', '-')


def anchors(path):
    found, fenced = set(), False
    for line in path.read_text(encoding='utf-8').split('\n'):
        if line.startswith('```'):
            fenced = not fenced
        elif not fenced and (m := re.match(r'#{1,6}\s+(.*?)\s*#*$', line)):
            found.add(slug(m.group(1)))
    return found


def resolve(target, base, errors, where, root=ROOT):
    path_part, _, frag = target.partition('#')
    path = (base / path_part).resolve() if path_part else base
    if path_part and not path.exists():
        errors.append(f'{where}: missing path {target}')
    elif frag and path.suffix == '.md' and frag.lower() not in anchors(path):
        errors.append(f'{where}: missing anchor #{frag} in {path.relative_to(root)}')


def check(root=ROOT):
    errors = []
    for md in [root / 'README.md', *sorted((root / 'docs').glob('*.md'))]:
        text = re.sub(r'```.*?```', '', md.read_text(encoding='utf-8'), flags=re.S)
        for target in MD_LINK.findall(text):
            if re.match(r'[a-z][a-z0-9+.-]*:', target):
                continue
            resolve(target, md if target.startswith('#') else md.parent, errors, md.relative_to(root), root)
    for name in ('index.html', 'llms.txt'):
        f = root / 'docs' / name
        for rel in BLOB_URL.findall(f.read_text(encoding='utf-8')):
            resolve(rel.rstrip('.,;'), root, errors, f'docs/{name}', root)
    return errors


if __name__ == '__main__':
    problems = check()
    for p in problems:
        print('FAIL ' + p)
    print('doc links: ' + ('FAILED' if problems else 'OK'))
    sys.exit(1 if problems else 0)
