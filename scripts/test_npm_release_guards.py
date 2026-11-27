#!/usr/bin/env python3
"""Exercise publish gates against local Git refs; no network or credentials."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = (ROOT / 'VERSION').read_text().strip()  # the single source; never a literal
TAG = 'v' + VERSION
NPM = subprocess.run(['node', '--input-type=module', '-e', "import {tagToNpm} from './launcher/lib/version.js'; console.log(tagToNpm(process.argv[1]))", TAG],
                     cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
# Deliberately wrong identities for the mismatch tests: a valid but far-future date (YY = 99),
# never a plausible release, so they can't collide with VERSION on a release day.
WRONG_TAG = 'v99.12.31'
WRONG_NPM = '99.1231.0'
assert WRONG_TAG != TAG and WRONG_NPM != NPM


class NpmReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='marc-publish-gates-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for name in ['.claude-plugin/marketplace.json', '.github/plugin/marketplace.json',
                     '.agents/plugins/marketplace.json', 'launcher/package.json',
                     'launcher/release.json', 'launcher/lib/version.js', 'VERSION',
                     'scripts/bump_version.py', 'scripts/prepare_npm_release.sh']:
            self.copy(name)
        for harness in ['claude-code', 'copilot', 'codex', 'antigravity']:
            self.copy(f'harnesses/{harness}/marc/' + ('.claude-plugin/plugin.json' if harness == 'claude-code' else 'plugin.json'))
        self.git('init', '-q', '-b', 'main')
        self.git('add', '.')
        self.git('commit', '-qm', 'reviewed release')
        self.sha = self.git('rev-parse', 'HEAD').strip()
        # Fetch is intentionally real, but its remote is the local fixture.
        self.git('remote', 'add', 'origin', str(self.repo))

    def copy(self, name):
        target = self.repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)

    def git(self, *args):
        return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@localhost', *args], cwd=self.repo,
                              capture_output=True, text=True, check=True).stdout

    def gate(self, sha=None, tag=TAG):
        return subprocess.run(['bash', 'scripts/prepare_npm_release.sh'], cwd=self.repo,
                              env={**os.environ, 'RELEASE_SHA': sha or self.sha, 'RELEASE_TAG': tag},
                              capture_output=True, text=True)

    def next_commit(self):
        (self.repo / 'new').write_text('unreviewed or later commit')
        self.git('add', 'new')
        self.git('commit', '-qm', 'next commit')
        return self.git('rev-parse', 'HEAD').strip()

    def test_reviewed_commit_embeds_exact_identity(self):
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads((self.repo / 'launcher/release.json').read_text()),
                         {'tag': TAG, 'npm': NPM, 'sha': self.sha})

    def test_reviewed_ancestor_is_allowed(self):
        self.next_commit()
        self.git('checkout', '-q', '--detach', self.sha)
        result = self.gate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_unmerged_commit_is_rejected(self):
        self.git('checkout', '-q', '-b', 'unreviewed')
        sha = self.next_commit()
        result = self.gate(sha)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('not an ancestor', result.stderr)
        self.assertIsNone(json.loads((self.repo / 'launcher/release.json').read_text())['sha'])

    def test_moved_checkout_is_rejected_before_fetch(self):
        self.next_commit()
        result = self.gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Checkout/release SHA mismatch', result.stderr)

    def test_wrong_tag_is_rejected(self):
        result = self.gate(tag=WRONG_TAG)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Tag/manifest mismatch', result.stderr)

    def publish_gate(self):
        return subprocess.run(['bash', 'scripts/prepare_npm_release.sh'], cwd=self.repo,
                              env={**os.environ, 'RELEASE_SHA': self.sha, 'RELEASE_TAG': TAG,
                                   'REQUIRE_RELEASE_TAG': 'true'}, capture_output=True, text=True)

    def test_publish_requires_existing_tag(self):
        self.assertNotEqual(self.publish_gate().returncode, 0)

    def test_publish_accepts_annotated_tag_at_target(self):
        self.git('-c', 'tag.gpgsign=false', 'tag', '-a', TAG, '-m', 'release', self.sha)
        result = self.publish_gate()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_publish_rejects_tag_moved_to_different_commit(self):
        later = self.next_commit()
        self.git('-c', 'tag.gpgsign=false', 'tag', '-a', TAG, '-m', 'moved', later)
        self.git('checkout', '-q', '--detach', self.sha)
        result = self.publish_gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Tag/release SHA mismatch', result.stderr)
        self.assertIsNone(json.loads((self.repo / 'launcher/release.json').read_text())['sha'])

    def test_wrong_package_version_is_rejected(self):
        path = self.repo / 'launcher/package.json'
        pkg = json.loads(path.read_text())
        pkg['version'] = WRONG_NPM
        path.write_text(json.dumps(pkg))
        result = self.gate()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('npm form of VERSION', result.stderr)  # refused for the version mismatch, not anything else


if __name__ == '__main__':
    unittest.main()
