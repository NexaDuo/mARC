#!/usr/bin/env python3
"""Plan an immutable release from a main push or a main workflow_dispatch.

Only HTTP 404 means absent: auth, rate-limit and registry outages fail closed.
The pure make_plan function is shared with offline tests.
"""
import argparse
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from bump_version import check, read_version, tag_to_npm

NPM_FLOOR = (26, 11, 25, 2)  # first launcher release; (YY, MM, DD, N)


def make_plan(version, event, sha, tag_sha, release_exists, npm_exists, *,
              tag_is_ancestor=False, npm_latest=None):
    tag = 'v' + version
    npm = tag_to_npm(tag)  # Strict calendar validation also makes outputs single-line.
    if event not in ('push', 'workflow_dispatch'):
        raise ValueError('release requires push or workflow_dispatch on main')
    if sha is None:
        raise ValueError('missing main commit SHA')
    for value in (sha, tag_sha):
        if value is not None and not re.fullmatch(r'[a-f0-9]{40}', value):
            raise ValueError('invalid release commit SHA')
    if tag_sha and not tag_is_ancestor:
        raise ValueError(f'{tag} commit is not an ancestor of origin/main; refusing to reuse a tag')
    target = tag_sha or sha
    parts = [int(p) for p in version.split('.')]
    release_key = tuple(parts + [0] * (4 - len(parts)))
    return dict(tag=tag, npm=npm, sha=target, need_tag=tag_sha is None,
                need_release=not release_exists,
                need_npm=release_key >= NPM_FLOOR
                and npm_publish_needed(npm, npm_exists, npm_latest))


def npm_publish_needed(version, exists, latest):
    # An existing exact version is a no-op, even after a newer release shipped.
    if exists:
        return False
    def parts(value):
        if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
            raise ValueError('invalid npm version; refusing to publish')
        return tuple(map(int, value.split('.')))
    target = parts(version)
    if latest is not None and target < parts(latest):
        raise ValueError(f'refusing to publish {version} below npm latest {latest}; no backfill path in v1')
    return True


def npm_state(version):
    package = json_or_none(f'https://registry.npmjs.org/@nexaduo%2fmarc/{version}')
    latest = json_or_none('https://registry.npmjs.org/@nexaduo%2fmarc/latest')
    return package is not None, latest['version'] if latest is not None else None


def check_npm(version):
    # Recheck immediately before npm publish: a failed-job rerun keeps its old plan.
    exists, latest = npm_state(version)
    needed = npm_publish_needed(version, exists, latest)
    if needed and latest is None:
        raise ValueError('npm package is absent; bootstrap is required. See docs/marc/2026-11-25-decision-npx-launcher.md#bootstrap')
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        output.write(f'publish={str(needed).lower()}\n')
    if not needed:
        print(f'npm {version} already published; successful no-op')


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
    check(Path('.'))  # fail closed: any VERSION/manifest drift fails the run, naming the file
    version = read_version(Path('.'))
    tag = 'v' + version
    npm = tag_to_npm(tag)
    tag_sha = remote_tag_sha(tag)
    repo = os.environ['GITHUB_REPOSITORY']
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', repo):
        raise ValueError('invalid GitHub repository')
    target = tag_sha or os.environ['GITHUB_SHA']
    if not re.fullmatch(r'[a-f0-9]{40}', target):
        raise ValueError('invalid release commit SHA')
    git('fetch', '--no-tags', 'origin', 'refs/heads/main:refs/remotes/origin/main')
    git('fetch', '--no-tags', 'origin', target)
    try:
        git('merge-base', '--is-ancestor', target, 'origin/main')
    except subprocess.CalledProcessError as exc:
        raise ValueError('release target is not an ancestor of origin/main') from exc
    release = json_or_none(f'https://api.github.com/repos/{repo}/releases/tags/{quote(tag, safe="")}', os.environ['GH_TOKEN'])
    exists, latest = npm_state(npm)
    plan = make_plan(version, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_SHA'],
                     tag_sha, release is not None, exists, tag_is_ancestor=True, npm_latest=latest)
    with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
        for key, value in plan.items():
            output.write(f'{key}={str(value).lower() if isinstance(value, bool) else value}\n')
    print(json.dumps(plan, sort_keys=True))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-npm', metavar='VERSION')
    args = parser.parse_args()
    if args.check_npm:
        check_npm(args.check_npm)
    else:
        main()
