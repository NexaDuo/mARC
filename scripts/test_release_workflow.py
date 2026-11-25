#!/usr/bin/env python3
"""Parse the release DAG and lock its trust boundaries (offline)."""
import re
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


class ReleaseWorkflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # BaseLoader preserves 'on' as a string (YAML 1.1 treats it as True).
        cls.workflow = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
        cls.jobs = cls.workflow['jobs']

    def test_triggers_and_serialization(self):
        self.assertEqual(set(self.workflow['on']), {'push', 'workflow_dispatch'})
        self.assertEqual(self.workflow['on']['push'], {'branches': ['main']})
        self.assertIn(self.workflow['on']['workflow_dispatch'], ('', None))
        self.assertEqual(self.workflow['concurrency'], {'group': 'release', 'cancel-in-progress': 'false'})
        self.assertFalse((ROOT / '.github/workflows/npm-publish.yml').exists())

    def test_exact_permissions(self):
        self.assertEqual(self.workflow['permissions'], {'contents': 'read'})
        expected = {'plan': {'contents': 'read'}, 'test': {'contents': 'read'},
                    'tag': {'contents': 'write'}, 'github-release': {'contents': 'write'},
                    'publish': {'contents': 'read', 'id-token': 'write'}}
        self.assertEqual({name: job['permissions'] for name, job in self.jobs.items()}, expected)
        self.assertEqual(self.jobs['publish']['environment'], 'npm-publish')

    def test_actions_pinned_and_no_run_interpolation(self):
        # No constants need expression templating in run scripts: empty allowlist.
        allowed_run_expressions = set()
        for name, job in self.jobs.items():
            for step in job['steps']:
                with self.subTest(job=name, step=step.get('name', step.get('uses'))):
                    if 'uses' in step:
                        self.assertRegex(step['uses'], r'^[^@]+@[a-f0-9]{40}$')
                    for expr in re.findall(r'\$\{\{.*?\}\}', step.get('run', ''), re.S):
                        self.assertIn(expr, allowed_run_expressions)
                    self.assertNotIn('${{', re.sub(r'\$\{\{.*?\}\}', '', step.get('run', ''), flags=re.S))

    def test_credentials_only_persist_in_tag_job(self):
        for name, job in self.jobs.items():
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/checkout@'):
                    self.assertEqual(step['with']['persist-credentials'], 'true' if name == 'tag' else 'false')
        self.assertEqual(self.jobs['plan']['steps'][0]['with']['ref'], '${{ github.sha }}')
        self.assertEqual(self.jobs['test']['steps'][0]['with']['ref'], '${{ needs.plan.outputs.sha }}')
        self.assertEqual(self.jobs['publish']['steps'][0]['with']['ref'], 'refs/tags/${{ needs.plan.outputs.tag }}')

    def test_dag_handles_skips_without_bypassing_failures(self):
        self.assertEqual(self.jobs['test']['needs'], 'plan')
        self.assertEqual(self.jobs['tag']['needs'], ['plan', 'test'])
        self.assertEqual(self.jobs['tag']['if'], "needs.plan.outputs.need_tag == 'true'")
        for name, output in [('github-release', 'need_release'), ('publish', 'need_npm')]:
            job = self.jobs[name]
            self.assertEqual(job['needs'], ['plan', 'tag'])
            for guard in ('!cancelled()', '!failure()', "needs.plan.result == 'success'",
                          "needs.tag.result == 'success'", "needs.tag.result == 'skipped'",
                          f"needs.plan.outputs.{output} == 'true'"):
                self.assertIn(guard, job['if'])
        test_script = '\n'.join(s.get('run', '') for s in self.jobs['test']['steps'])
        self.assertIn('node --test launcher/', test_script)
        self.assertIn('bash scripts/prepare_npm_release.sh', test_script)
        self.assertNotIn('node --test', '\n'.join(s.get('run', '') for s in self.jobs['publish']['steps']))

    def test_annotated_tag_race_guard_and_notes_file(self):
        script = self.jobs['tag']['steps'][-1]['run']
        self.assertIn('tag -a "$RELEASE_TAG" "$RELEASE_SHA"', script)
        self.assertLess(script.index('git ls-remote'), script.index('git push'))
        self.assertNotIn('--force', script)
        release = self.jobs['github-release']['steps']
        self.assertEqual(release[0]['with']['ref'], '${{ github.event.repository.default_branch }}')
        self.assertEqual(release[-1]['env']['RELEASE_TAG'], '${{ needs.plan.outputs.tag }}')
        self.assertEqual(release[-1]['run'].count('--notes-file release-notes.md'), 2)
        self.assertIn('--verify-tag', release[-1]['run'])


class TagPushRaces(unittest.TestCase):
    """Run the real workflow shell against a local origin; no network/token."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='marc-tag-race-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.origin = self.root / 'origin.git'
        self.repo = self.root / 'runner'
        self.git(self.root, 'init', '--bare', '-q', str(self.origin))
        self.git(self.root, 'clone', '-q', str(self.origin), str(self.repo))
        (self.repo / 'file').write_text('reviewed version bump')
        self.git(self.repo, 'add', 'file')
        self.git(self.repo, 'commit', '-qm', 'release')
        self.sha = self.git(self.repo, 'rev-parse', 'HEAD').strip()
        workflow = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
        self.script = workflow['jobs']['tag']['steps'][-1]['run']

    def git(self, cwd, *args):
        return subprocess.run(['git', '-c', 'commit.gpgsign=false', '-c', 'user.name=Fixture',
                               '-c', 'user.email=fixture@localhost', *args], cwd=cwd,
                              text=True, capture_output=True, check=True).stdout

    def run_tag(self):
        return subprocess.run(['bash', '-c', self.script], cwd=self.repo,
                              env={**os.environ, 'RELEASE_TAG': 'v26.11.26', 'RELEASE_SHA': self.sha},
                              text=True, capture_output=True)

    def test_new_tag_is_annotated_at_exact_commit(self):
        result = self.run_tag()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.git(self.origin, 'cat-file', '-t', 'refs/tags/v26.11.26').strip(), 'tag')
        self.assertEqual(self.git(self.origin, 'rev-parse', 'v26.11.26^{commit}').strip(), self.sha)

    def test_remote_tag_appearing_after_plan_is_never_replaced(self):
        # The runner has not fetched this new remote tag; its local creation
        # succeeds, so the explicit remote recheck has to catch the race.
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.git(self.origin, '-c', 'tag.gpgsign=false', 'tag', '-a', 'v26.11.26', self.sha, '-m', 'other creator')
        original = self.git(self.origin, 'rev-parse', 'refs/tags/v26.11.26').strip()
        result = self.run_tag()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Tag appeared after planning', result.stdout)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'refs/tags/v26.11.26').strip(), original)


if __name__ == '__main__':
    unittest.main()
