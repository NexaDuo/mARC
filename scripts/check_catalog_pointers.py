#!/usr/bin/env python3
"""Release parity gate: four versions and three harness-specific pointers."""
import json
import re
from datetime import date
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOGS = {
    'claude-code': '.claude-plugin/marketplace.json',
    'copilot': '.github/plugin/marketplace.json',
    'codex': '.agents/plugins/marketplace.json',
}


def tag_to_npm(tag):
    """Same calendar mapping and validation as launcher/lib/version.js."""
    match = re.fullmatch(r"v([1-9][0-9])\.([1-9][0-9]?)\.([1-9][0-9]?)(?:\.([1-9][0-9]*))?", tag)
    if not match:
        raise ValueError('unsupported calendar tag')
    y, month, day, n = (int(v or 0) for v in match.groups())
    if date(2000 + y, month, day) < date(2026, 11, 25) or n > 9007199254740991:
        raise ValueError('tag below launcher mapping floor or unsafe release number')
    return f'{y}.{month * 100 + day}.{n}'


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
    package = json.loads((root / 'launcher/package.json').read_text())
    release = json.loads((root / 'launcher/release.json').read_text())
    if package['version'] != tag_to_npm(tag):
        raise ValueError('launcher/package.json version does not match plugin tag mapping')
    if release != {'tag': tag, 'npm': package['version'], 'sha': None}:
        raise ValueError('launcher/release.json must match tag/npm and have a null source SHA')
    return tag


if __name__ == '__main__':
    try:
        print('Catalog pointer parity: OK (' + check(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT) + ')')
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(str(exc))
