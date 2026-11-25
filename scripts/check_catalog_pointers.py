#!/usr/bin/env python3
"""Release parity gate: four versions and three harness-specific pointers."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = {
    'claude-code': '.claude-plugin/marketplace.json',
    'copilot': '.github/plugin/marketplace.json',
    'codex': '.agents/plugins/marketplace.json',
}


def check(root=ROOT):
    versions = {}
    for harness in (*CATALOGS, 'antigravity'):
        manifest = root / 'harnesses' / harness / 'marc' / ('.claude-plugin/plugin.json' if harness == 'claude-code' else 'plugin.json')
        versions[harness] = json.loads(manifest.read_text())['version']
    if len(set(versions.values())) != 1:
        raise ValueError(f'harness version drift: {versions}')
    tag = 'v' + versions['claude-code']
    for harness, catalog in CATALOGS.items():
        data = json.loads((root / catalog).read_text())
        plugins = [p for p in data['plugins'] if p['name'] == 'marc']
        if len(plugins) != 1:
            raise ValueError(f'{catalog}: expected one marc entry')
        source = {'source': 'github', 'repo': 'NexaDuo/mARC'} if harness == 'copilot' else {'source': 'git-subdir', 'url': 'NexaDuo/mARC'}
        source.update(path=f'harnesses/{harness}/marc', ref=tag)
        if plugins[0]['source'] != source:
            raise ValueError(f'{catalog}: expected {source}, got {plugins[0]["source"]}')
    return tag


if __name__ == '__main__':
    try:
        print('Catalog pointer parity: OK (' + check(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT) + ')')
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(str(exc))
