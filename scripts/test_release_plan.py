#!/usr/bin/env python3
"""Offline release planning, including remote lookups failing closed."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_plan as rp

A, B = 'a' * 40, 'b' * 40


class ReleasePlan(unittest.TestCase):
    def plan(self, **changes):
        inputs = dict(version='26.11.26', event='push', sha=A, tag_sha=None,
                      release_exists=False, npm_exists=False, tag_is_ancestor=True)
        inputs.update(changes)
        return rp.make_plan(**inputs)

    def test_new_release(self):
        self.assertEqual(self.plan(), dict(tag='v26.11.26', npm='26.1126.0', sha=A,
                                          need_tag=True, need_release=True, need_npm=True))

    def test_push_reuses_existing_ancestor_as_green_noop(self):
        plan = self.plan(tag_sha=B, release_exists=True, npm_exists=True)
        self.assertEqual(plan['sha'], B)
        self.assertFalse(any(plan[k] for k in ('need_tag', 'need_release', 'need_npm')))

    def test_push_retries_missing_surfaces_at_existing_ancestor(self):
        plan = self.plan(tag_sha=B)
        self.assertEqual(plan['sha'], B)
        self.assertEqual([plan[k] for k in ('need_tag', 'need_release', 'need_npm')], [False, True, True])

    def test_existing_tag_outside_main_fails(self):
        for event in ('push', 'workflow_dispatch'):
            with self.subTest(event=event), self.assertRaisesRegex(ValueError, 'not an ancestor'):
                self.plan(event=event, tag_sha=B, tag_is_ancestor=False)

    def test_npm_latest_cannot_move_backwards(self):
        for latest in ('26.1126.1', '26.1127.0', '27.101.0'):
            with self.subTest(latest=latest), self.assertRaisesRegex(ValueError, 'no backfill path'):
                self.plan(npm_latest=latest)
        self.assertTrue(self.plan(npm_latest='26.930.9')['need_npm'])
        self.assertFalse(self.plan(npm_latest='26.1127.0', npm_exists=True)['need_npm'])

    def test_npm_latest_malformed_fails_closed(self):
        for latest in ('bad', '26.1127.0\n', '26.1127.0-beta', ''):
            with self.subTest(latest=latest), self.assertRaises(ValueError):
                self.plan(npm_latest=latest)

    def test_dispatch_reuses_existing_tag_commit(self):
        plan = self.plan(event='workflow_dispatch', tag_sha=B)
        self.assertEqual(plan['sha'], B)
        self.assertFalse(plan['need_tag'])

    def test_dispatch_without_tag_uses_main_sha(self):
        self.assertEqual(self.plan(event='workflow_dispatch')['sha'], A)

    def test_completed_rerun_is_noop(self):
        plan = self.plan(tag_sha=A, release_exists=True, npm_exists=True)
        self.assertFalse(any(plan[k] for k in ('need_tag', 'need_release', 'need_npm')))

    def test_bootstrap_rerun_only_needs_npm(self):
        plan = self.plan(event='workflow_dispatch', tag_sha=B, release_exists=True)
        self.assertEqual([plan[k] for k in ('need_tag', 'need_release', 'need_npm')], [False, False, True])

    def test_npm_floor(self):
        for version, needed in [('26.11.25', False), ('26.11.25.9', False),
                                ('26.11.26', True), ('26.11.26.1', True), ('27.1.1', True)]:
            with self.subTest(version=version):
                self.assertEqual(self.plan(version=version)['need_npm'], needed)
        self.assertFalse(self.plan(npm_exists=True)['need_npm'])

    def test_outputs_reject_injection_and_invalid_identity(self):
        for version in ['26.11.26\nneed_tag=true', '26.11.26$(id)', '26.02.30']:
            with self.assertRaises(ValueError):
                self.plan(version=version)
        for sha in [None, 'main', A + '\n', '--help']:
            with self.assertRaises(ValueError):
                self.plan(sha=sha)
        with self.assertRaises(ValueError):
            self.plan(event='pull_request')

    def test_remote_tag_peels_annotated_object(self):
        with patch.object(rp, 'git', return_value=f'{A}\trefs/tags/v26.11.26\n{B}\trefs/tags/v26.11.26^{{}}'):
            self.assertEqual(rp.remote_tag_sha('v26.11.26'), B)
        with patch.object(rp, 'git', return_value=f'{A}\trefs/tags/v26.11.26'):
            self.assertEqual(rp.remote_tag_sha('v26.11.26'), A)
        with patch.object(rp, 'git', return_value=''):
            self.assertIsNone(rp.remote_tag_sha('v26.11.26'))
        with patch.object(rp, 'git', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                rp.remote_tag_sha('v26.11.26')

    def test_only_404_means_missing(self):
        for code in (404, 401, 403, 429, 500):
            with self.subTest(code=code), patch.object(rp, 'urlopen', side_effect=HTTPError('url', code, 'failure', {}, None)):
                if code == 404:
                    self.assertIsNone(rp.json_or_none('https://example.invalid'))
                else:
                    with self.assertRaises(HTTPError):
                        rp.json_or_none('https://example.invalid')

    def test_main_ref_required(self):
        with patch.dict(rp.os.environ, {'GITHUB_REF': 'refs/heads/unreviewed'}):
            with self.assertRaisesRegex(ValueError, 'main'):
                rp.main()

    def test_publish_rechecks_exact_version_and_latest(self):
        for exists, latest, expected in [(True, '26.1127.0', 'false'), (False, '26.1125.0', 'true')]:
            with self.subTest(exists=exists), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'output'
                with patch.dict(rp.os.environ, {'GITHUB_OUTPUT': str(output)}), \
                        patch.object(rp, 'npm_state', return_value=(exists, latest)):
                    rp.check_npm('26.1126.0')
                self.assertEqual(output.read_text(), f'publish={expected}\n')

    def test_publish_refuses_backfill_and_missing_bootstrap_before_outputs(self):
        for latest, message in [('26.1127.0', 'no backfill path'), (None, 'bootstrap is required')]:
            with self.subTest(latest=latest), tempfile.TemporaryDirectory() as temp:
                output = Path(temp) / 'output'
                with patch.dict(rp.os.environ, {'GITHUB_OUTPUT': str(output)}), \
                        patch.object(rp, 'npm_state', return_value=(False, latest)):
                    with self.assertRaisesRegex(ValueError, message):
                        rp.check_npm('26.1126.0')
                self.assertFalse(output.exists())

    def test_npm_state_uses_exact_and_latest_endpoints(self):
        with patch.object(rp, 'json_or_none', side_effect=[None, {'version': '26.1127.0'}]) as lookup:
            self.assertEqual(rp.npm_state('26.1126.0'), (False, '26.1127.0'))
            self.assertEqual([call.args[0] for call in lookup.call_args_list], [
                'https://registry.npmjs.org/@nexaduo%2fmarc/26.1126.0',
                'https://registry.npmjs.org/@nexaduo%2fmarc/latest'])

    def test_main_push_existing_tag_reuses_checked_commit(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            env = dict(GITHUB_REF='refs/heads/main', GITHUB_SHA=A, GITHUB_EVENT_NAME='push',
                       GITHUB_REPOSITORY='NexaDuo/mARC', GH_TOKEN='fixture', GITHUB_OUTPUT=str(output))
            with patch.dict(rp.os.environ, env), patch.object(rp, 'remote_tag_sha', return_value=B), \
                    patch.object(rp, 'json_or_none', return_value={'id': 1}), \
                    patch.object(rp, 'npm_state', return_value=(True, '26.1127.0')), \
                    patch.object(rp, 'git', return_value='') as git, \
                    patch.object(rp.Path, 'read_text', return_value=json.dumps({'version': '26.11.26'})):
                rp.main()
            git.assert_any_call('merge-base', '--is-ancestor', B, 'origin/main')
            self.assertIn(f'sha={B}\n', output.read_text())
            for key in ('need_tag', 'need_release', 'need_npm'):
                self.assertIn(f'{key}=false\n', output.read_text())

    def test_target_must_be_main_ancestor_before_outputs(self):
        import subprocess
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / 'output'
            env = dict(GITHUB_REF='refs/heads/main', GITHUB_SHA=A, GITHUB_EVENT_NAME='push',
                       GITHUB_REPOSITORY='NexaDuo/mARC', GH_TOKEN='fixture', GITHUB_OUTPUT=str(output))
            def git(*args):
                if args[0] == 'merge-base':
                    raise subprocess.CalledProcessError(1, ['git', *args])
                return ''
            with patch.dict(rp.os.environ, env), patch.object(rp, 'remote_tag_sha', return_value=B), \
                    patch.object(rp, 'json_or_none', return_value=None), patch.object(rp, 'git', side_effect=git), \
                    patch.object(rp.Path, 'read_text', return_value=json.dumps({'version': '26.11.26'})):
                with self.assertRaisesRegex(ValueError, 'not an ancestor'):
                    rp.main()
            self.assertFalse(output.exists())


if __name__ == '__main__':
    unittest.main()
