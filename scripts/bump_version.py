#!/usr/bin/env python3
"""Single-source release version: stamp every copy from VERSION, or verify them.

    bump_version.py <ver>   write VERSION and stamp every copy (e.g. 26.12.1)
    bump_version.py --check fail, naming the file, on any divergence from VERSION

VERSION (repo root, one line) is the only hand-edited version. Stamped copies:
the four harness plugin.json, the three catalog `ref` pointers, launcher/package.json,
and launcher/release.json (tag/npm; sha stays null until the publish job fills it).
The npm form is the Python port of launcher/lib/version.js `tagToNpm`.
"""
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
MANIFESTS = {h: f'harnesses/{h}/marc/' + ('.claude-plugin/plugin.json' if h == 'claude-code' else 'plugin.json')
             for h in ('claude-code', 'copilot', 'codex', 'antigravity')}
PACKAGE = 'launcher/package.json'
RELEASE = 'launcher/release.json'


def tag_to_npm(tag):
    """Same calendar mapping and validation as launcher/lib/version.js."""
    match = re.fullmatch(r"v([1-9][0-9])\.([1-9][0-9]?)\.([1-9][0-9]?)(?:\.([1-9][0-9]*))?", tag)
    if not match:
        raise ValueError('unsupported calendar tag')
    y, month, day, n = (int(v or 0) for v in match.groups())
    if date(2000 + y, month, day) < date(2026, 11, 25) or n > 9007199254740991:
        raise ValueError('tag below launcher mapping floor or unsafe release number')
    return f'{y}.{month * 100 + day}.{n}'


def read_version(root=ROOT):
    """VERSION must be exactly one line holding a valid calendar version."""
    text = (root / 'VERSION').read_text()
    version = text.strip()
    if text != version + '\n' or not version:
        raise ValueError('VERSION must be exactly one line')
    tag_to_npm('v' + version)  # validates the calendar format
    return version


def stamp(version, root=ROOT):
    """Write VERSION and every copy. Idempotent; returns the files it owns."""
    tag = 'v' + version
    npm = tag_to_npm(tag)
    plan = [('VERSION', None, None)]
    plan += [(m, r'("version"\s*:\s*")[^"]*(")', version) for m in MANIFESTS.values()]
    plan += [(c, r'("ref"\s*:\s*")[^"]*(")', tag) for c in CATALOGS.values()]
    plan.append((PACKAGE, r'("version"\s*:\s*")[^"]*(")', npm))
    for name, pattern, value in plan:
        path = root / name
        if pattern is None:
            path.write_text(version + '\n')
            continue
        text, count = re.subn(pattern, lambda m: m.group(1) + value + m.group(2), path.read_text(), count=1)
        if count != 1:
            raise ValueError(f'{name}: no version field to stamp')
        path.write_text(text)
    (root / RELEASE).write_text(json.dumps({'tag': tag, 'sha': None, 'npm': npm}, indent=2) + '\n')
    return [n for n, _, _ in plan] + [RELEASE]


def check(root=ROOT):
    """Raise ValueError naming the first file that diverges from VERSION; return the tag."""
    version = read_version(root)
    tag = 'v' + version
    for manifest in MANIFESTS.values():
        found = json.loads((root / manifest).read_text())['version']
        if found != version:
            raise ValueError(f'{manifest}: version {found} != VERSION {version}')
    for harness, catalog in CATALOGS.items():
        data = json.loads((root / catalog).read_text())
        plugins = [p for p in data['plugins'] if p['name'] == 'marc']
        if len(plugins) != 1:
            raise ValueError(f'{catalog}: expected one marc entry')
        source = {'source': 'github', 'repo': 'NexaDuo/mARC'} if harness == 'copilot' else {'source': 'git-subdir', 'url': 'NexaDuo/mARC'}
        source.update(path=f'harnesses/{harness}/marc', ref=tag)
        if plugins[0]['source'] != source:
            raise ValueError(f'{catalog}: expected {source}, got {plugins[0]["source"]}')
    npm = tag_to_npm(tag)
    package = json.loads((root / PACKAGE).read_text())
    if package['version'] != npm:
        raise ValueError(f'{PACKAGE}: version {package["version"]} != {npm} (npm form of VERSION)')
    release = json.loads((root / RELEASE).read_text())
    if release != {'tag': tag, 'npm': npm, 'sha': None}:
        raise ValueError(f'{RELEASE}: must be tag {tag}, npm {npm} and a null source SHA')
    return tag


if __name__ == '__main__':
    args = sys.argv[1:]
    try:
        if args and args[0] != '--check':
            stamp(args[0])
            print('Stamped ' + check() + '; commit VERSION and the stamped files')
        else:
            print('Version parity: OK (' + check(Path(args[1]) if len(args) > 1 else ROOT) + ')')
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(str(exc))
