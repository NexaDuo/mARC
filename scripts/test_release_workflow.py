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
                    'tag': {'contents': 'read'}, 'github-release': {'contents': 'write'},
                    'publish': {'contents': 'read', 'id-token': 'write'}}
        self.assertEqual({name: job['permissions'] for name, job in self.jobs.items()}, expected)
        self.assertEqual(self.jobs['publish']['environment'], 'npm-publish')
        self.assertEqual(self.jobs['tag']['environment'], 'release-tag')

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

    def test_checkout_credentials_never_persist(self):
        for name, job in self.jobs.items():
            for step in job['steps']:
                if step.get('uses', '').startswith('actions/checkout@'):
                    self.assertEqual(step['with']['persist-credentials'], 'false')
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
        script = self.jobs['tag']['steps'][-2]['run']
        self.assertIn('tag -a "$RELEASE_TAG" "$RELEASE_SHA"', script)
        self.assertLess(script.index('git ls-remote'), script.index('git push'))
        self.assertNotIn('--force', script)
        release = self.jobs['github-release']['steps']
        self.assertEqual(release[0]['with']['ref'], '${{ github.event.repository.default_branch }}')
        self.assertEqual(release[-1]['env']['RELEASE_TAG'], '${{ needs.plan.outputs.tag }}')
        self.assertEqual(release[-1]['run'].count('--notes-file release-notes.md'), 2)
        self.assertIn('--verify-tag', release[-1]['run'])

    def test_deploy_key_scope_pinning_and_cleanup(self):
        for name, job in self.jobs.items():
            if name != 'tag':
                self.assertNotIn('secrets.', str(job))
        tag = self.jobs['tag']['steps']
        setup, push, cleanup = tag[-3:]
        self.assertEqual(setup['env'], {'RELEASE_TAG_DEPLOY_KEY': '${{ secrets.RELEASE_TAG_DEPLOY_KEY }}'})
        self.assertIn('umask 077', setup['run'])
        self.assertIn('chmod 600', setup['run'])
        self.assertNotIn('ssh-keyscan', setup['run'])
        for algorithm in ('ssh-ed25519', 'ecdsa-sha2-nistp256', 'ssh-rsa'):
            self.assertIn(f'github.com {algorithm} ', setup['run'])
        self.assertIn('https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/githubs-ssh-key-fingerprints', setup['run'])
        for option in ('IdentitiesOnly=yes', 'IdentityAgent=none', 'StrictHostKeyChecking=yes',
                       'UserKnownHostsFile=', 'GlobalKnownHostsFile=/dev/null'):
            self.assertIn(option, push['env']['GIT_SSH_COMMAND'])
        self.assertEqual(cleanup['if'], 'always()')
        self.assertIn('"$RUNNER_TEMP/marc-release-tag-key"', cleanup['run'])

    def test_publish_guard_and_no_lifecycle_scripts(self):
        guard = next(s for s in self.jobs['publish']['steps'] if s.get('id') == 'npm-check')
        publish = self.jobs['publish']['steps'][-1]
        self.assertIn('release_plan.py --check-npm "$RELEASE_NPM"', guard['run'])
        self.assertEqual(publish['if'], "steps.npm-check.outputs.publish == 'true'")
        self.assertIn('npm publish --access public --provenance --ignore-scripts', publish['run'])


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
        self.script = workflow['jobs']['tag']['steps'][-2]['run'].replace('git@github.com:NexaDuo/mARC.git', str(self.origin))

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

    def test_suffix_decoy_refs_are_ignored(self):
        # ls-remote patterns also match ref tails, so these decoys really are
        # returned by git. Neither an annotated peeled row nor a lightweight
        # tag may masquerade as the requested full ref.
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        for annotated in (True, False):
            with self.subTest(annotated=annotated):
                decoy = 'x/refs/tags/v26.11.26'
                args = ('-a', '-m', 'decoy') if annotated else ()
                self.git(self.origin, '-c', 'tag.gpgsign=false', 'tag', *args, decoy, self.sha)
                rows = self.git(self.repo, 'ls-remote', '--tags', str(self.origin),
                                'refs/tags/v26.11.26', 'refs/tags/v26.11.26^{}')
                self.assertIn('refs/tags/' + decoy, rows)
                result = self.run_tag()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertNotIn('successful no-op', result.stdout)
                self.assertEqual(self.git(self.origin, 'rev-parse', 'v26.11.26^{commit}').strip(), self.sha)
                self.git(self.repo, 'tag', '-d', 'v26.11.26')
                self.git(self.origin, 'tag', '-d', 'v26.11.26', decoy)

    def test_decoy_peeled_ref_cannot_override_exact_tag(self):
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        self.git(self.origin, '-c', 'tag.gpgsign=false', 'tag', 'v26.11.26', self.sha)
        (self.repo / 'file').write_text('decoy commit')
        self.git(self.repo, 'commit', '-qam', 'decoy')
        other = self.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/other')
        self.git(self.origin, '-c', 'tag.gpgsign=false', 'tag', '-a',
                 'x/refs/tags/v26.11.26', other, '-m', 'decoy')
        result = self.run_tag()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('successful no-op', result.stdout)

    def test_remote_tag_appearing_after_plan_is_never_replaced(self):
        # The runner has not fetched this new remote tag; its local creation
        # succeeds, so the explicit remote recheck has to catch the race.
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
        (self.repo / 'file').write_text('a different commit')
        self.git(self.repo, 'commit', '-qam', 'other')
        other = self.git(self.repo, 'rev-parse', 'HEAD').strip()
        self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/other')
        self.git(self.origin, '-c', 'tag.gpgsign=false', 'tag', '-a', 'v26.11.26', other, '-m', 'other creator')
        original = self.git(self.origin, 'rev-parse', 'refs/tags/v26.11.26').strip()
        result = self.run_tag()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Tag appeared after planning', result.stdout)
        self.assertEqual(self.git(self.origin, 'rev-parse', 'refs/tags/v26.11.26').strip(), original)

    def test_remote_tag_at_exact_commit_is_successful_noop(self):
        for annotated in (True, False):
            with self.subTest(annotated=annotated):
                self.git(self.repo, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
                args = ('-a', '-m', 'already pushed') if annotated else ()
                self.git(self.origin, '-c', 'tag.gpgsign=false', 'tag', *args, 'v26.11.26', self.sha)
                original = self.git(self.origin, 'rev-parse', 'refs/tags/v26.11.26')
                result = self.run_tag()
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('successful no-op', result.stdout)
                self.assertEqual(self.git(self.origin, 'rev-parse', 'refs/tags/v26.11.26'), original)
                self.git(self.origin, 'tag', '-d', 'v26.11.26')

    def test_rerun_with_local_and_remote_tag_is_noop(self):
        self.assertEqual(self.run_tag().returncode, 0)
        result = self.run_tag()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('successful no-op', result.stdout)


class SSHCredentialHandling(unittest.TestCase):
    def test_private_file_missing_key_and_cleanup(self):
        workflow = yaml.load((ROOT / '.github/workflows/release.yml').read_text(), Loader=yaml.BaseLoader)
        setup, _, cleanup = workflow['jobs']['tag']['steps'][-3:]
        with tempfile.TemporaryDirectory() as temp:
            env = {**os.environ, 'RUNNER_TEMP': temp, 'RELEASE_TAG_DEPLOY_KEY': ''}
            result = subprocess.run(['bash', '-c', setup['run']], env=env, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('RELEASE_TAG_DEPLOY_KEY is missing', result.stdout)
            self.assertFalse(list(Path(temp).iterdir()))
            secret = 'fixture-private-key-content'
            env['RELEASE_TAG_DEPLOY_KEY'] = secret
            result = subprocess.run(['bash', '-c', setup['run']], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn(secret, result.stdout + result.stderr)
            key = Path(temp) / 'marc-release-tag-key'
            self.assertEqual(key.stat().st_mode & 0o777, 0o600)
            self.assertEqual(key.read_text(), secret + '\n')
            # Parse the pinned public keys with OpenSSH; expected fingerprints
            # make accidental edits or copied obsolete RSA keys fail offline.
            hosts = Path(temp) / 'marc-release-tag-known-hosts'
            result = subprocess.run(['ssh-keygen', '-lf', str(hosts)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            for fingerprint in (
                    'SHA256:+DiY3wvvV6TuJJhbpZisF/zLDA0zPMSvHdkr4UvCOqU',
                    'SHA256:p2QAMXNIC1TJYWeIOttrVc98/R1BUFWu3/LiyKgUfQM',
                    'SHA256:uNiVztksCsDhcc0u9e8BujQXVUpKZIDTMczCvj3tD2s'):
                self.assertIn(fingerprint, result.stdout)
            result = subprocess.run(['bash', '-c', cleanup['run']], env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(list(Path(temp).iterdir()))


if __name__ == '__main__':
    unittest.main()
