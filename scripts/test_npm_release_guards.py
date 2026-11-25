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


class NpmReleaseGuards(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='marc-publish-gates-')
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        for name in ['.claude-plugin/marketplace.json', '.github/plugin/marketplace.json',
                     '.agents/plugins/marketplace.json', 'launcher/package.json',
                     'launcher/release.json', 'launcher/lib/version.js',
                     'scripts/check_catalog_pointers.py', 'scripts/prepare_npm_release.sh']:
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

    def gate(self, sha=None, tag='v26.11.26'):
        return subprocess.run(['bash', 'scripts/prepare_npm_release.sh'], cwd=self.repo,
                              env={**os.environ, 'GITHUB_SHA': sha or self.sha,
                                   'GITHUB_REF_NAME': tag, 'GITHUB_REF': 'refs/tags/' + tag},
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
                         {'tag': 'v26.11.26', 'npm': '26.1126.0', 'sha': self.sha})

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
        self.assertIn('Checkout/event SHA mismatch', result.stderr)

    def test_wrong_tag_is_rejected(self):
        result = self.gate(tag='v26.11.27')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Tag/manifest mismatch', result.stderr)

    def test_workflow_keeps_tests_out_of_oidc_job(self):
        workflow = (ROOT / '.github/workflows/npm-publish.yml').read_text()
        test_job, publish_job = workflow.split('  publish:\n')
        self.assertNotIn('id-token:', test_job)
        self.assertIn('node --test launcher/', test_job)
        self.assertIn('needs: test', publish_job)
        self.assertIn('environment: npm-publish', publish_job)
        self.assertIn('id-token: write', publish_job)
        self.assertNotIn('node --test', publish_job)
        self.assertEqual(workflow.count('run: bash scripts/prepare_npm_release.sh'), 2)


if __name__ == '__main__':
    unittest.main()
