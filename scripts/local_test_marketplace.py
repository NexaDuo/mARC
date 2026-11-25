#!/usr/bin/env python3
"""Copy the PR's harness trees and replace remote pointers in temp catalogs.

Production catalogs are never edited. Pass DEST [SOURCE] to prepare a CI copy;
callers own DEST's lifetime. Local directory catalogs exercise the PR tree,
not the previous tagged release (in particular Copilot's live local sources).
"""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = {
    'claude-code': '.claude-plugin/marketplace.json',
    'copilot': '.github/plugin/marketplace.json',
    'codex': '.agents/plugins/marketplace.json',
}


def prepare(destination, source=ROOT):
    destination, source = Path(destination), Path(source)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError('test marketplace destination must be empty')
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / 'harnesses', destination / 'harnesses', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for harness, catalog in CATALOGS.items():
        target = destination / catalog
        target.parent.mkdir(parents=True, exist_ok=True)
        data = json.loads((source / catalog).read_text())
        for plugin in data['plugins']:
            if plugin['name'] == 'marc':
                local = f'./harnesses/{harness}/marc'
                plugin['source'] = {'source': 'local', 'path': local} if harness == 'codex' else local
        target.write_text(json.dumps(data, indent=2) + '\n')
    return str(destination.resolve())


if __name__ == '__main__':
    print(prepare(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else ROOT))
