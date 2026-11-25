#!/usr/bin/env python3
"""Plan an immutable release from a main push or a main workflow_dispatch.

Only HTTP 404 means absent: auth, rate-limit and registry outages fail closed.
The pure make_plan function is shared with offline tests.
"""
import json
import os
import re
import subprocess
from datetime import date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from check_catalog_pointers import tag_to_npm

NPM_FLOOR = date(2026, 11, 26)


def make_plan(version, event, sha, tag_sha, release_exists, npm_exists):
    tag = 'v' + version
    npm = tag_to_npm(tag)  # Strict calendar validation also makes outputs single-line.
    if event not in ('push', 'workflow_dispatch'):
        raise ValueError('release requires push or workflow_dispatch on main')
    if sha is None:
        raise ValueError('missing main commit SHA')
    for value in (sha, tag_sha):
        if value is not None and not re.fullmatch(r'[a-f0-9]{40}', value):
            raise ValueError('invalid release commit SHA')
    target = tag_sha if event == 'workflow_dispatch' and tag_sha else sha
    if tag_sha and tag_sha != target:
        raise ValueError(f'{tag} already points at {tag_sha}, not {target}; refusing to move a tag')
    y, m, d = map(int, version.split('.')[:3])
    return dict(tag=tag, npm=npm, sha=target, need_tag=tag_sha is None,
                need_release=not release_exists,
                need_npm=date(2000 + y, m, d) >= NPM_FLOOR and not npm_exists)


def git(*args):
    return subprocess.run(['git', *args], text=True, capture_output=True, check=True).stdout.strip()


def remote_tag_sha(tag):
    # ls-remote returns both the annotated object and its peeled commit.
    rows = git('ls-remote', '--tags', 'origin', f'refs/tags/{tag}', f'refs/tags/{tag}^{{}}')
    refs = dict(line.split()[::-1] for line in rows.splitlines())
    return refs.get(f'refs/tags/{tag}^{{}}', refs.get(f'refs/tags/{tag}'))


def json_or_none(url, token=None):
    headers = {'Accept': 'application/json', 'User-Agent': 'marc-release-plan'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        with urlopen(Request(url, headers=headers), timeout=30) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def main():
    if os.environ['GITHUB_REF'] != 'refs/heads/main':
        raise ValueError('release must run on main')
    version = json.loads(Path('harnesses/claude-code/marc/.claude-plugin/plugin.json').read_text())['version']
    tag = 'v' + version
    npm = tag_to_npm(tag)
    tag_sha = remote_tag_sha(tag)
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('invalid GitHub repository')
    release = json_or_none(f'https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe="")}', os.environ['GH_TOKEN'])
    package = json_or_none(f'https://registry.npmjs.org/@nexaduo%2fmarc/{npm}')
    plan = make_plan(version, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_SHA'],
                     tag_sha, release is not None, package is not None)
    git('fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
    git('fetch', '--no-tags', 'origin', plan['sha'])
    try:
        git('merge-base', '--is-ancestor', plan['sha'], 'origin/main')
    except subprocess.CalledProcessError as exc:
        raise ValueError('release target is not an ancestor of origin/main') from exc
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in plan.items():
            output.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')
    print(json.dumps(plan, sort_keys=True))


if __name__ == '__main__':
    main()
