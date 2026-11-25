#!/usr/bin/env python3
"""Offline npm release verification fixtures (#424), no network or real git."""
import base64
import hashlib
import io
import json
import sys
import tarfile
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_verify as rv


class NpmPublished(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'launcher').mkdir()
        (self.root / 'launcher/package.json').write_text(json.dumps({'name': '@nexaduo/marc'}))

    def fixture(self, release=None):
        release = release or {'tag': 'v26.11.26.1', 'npm': '26.1126.1', 'sha': 'a' * 40}
        content = json.dumps(release).encode()
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w:gz') as archive:
            member = tarfile.TarInfo('package/release.json')
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        blob = out.getvalue()
        self.metadata = {'dist-tags': {'latest': '26.1126.1'}, 'versions': {'26.1126.1': {
            'version': '26.1126.1', 'dist': {
                'tarball': 'https://registry.npmjs.org/marc.tgz',
                'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(blob).digest()).decode(),
                'attestations': {'url': 'https://registry.npmjs.org/attestations'}}}}}
        self.attestations = {'attestations': [{'predicateType': 'https://slsa.dev/provenance/v1', 'bundle': {'content': 'fixture'}}]}
        self.blob = blob

    def check(self, tag="v26.11.26.1"):
        def download(url, limit):
            if url.endswith('.tgz'):
                return self.blob
            return json.dumps(self.attestations if url.endswith('attestations') else self.metadata).encode()
        with patch.object(rv, '_download', side_effect=download), patch.object(rv, '_run', return_value='a' * 40 + '\n') as run:
            result = rv.fetch_npm_published(tag, self.root)
            if result.ok:
                run.assert_called_once_with(['git', '-C', str(self.root), 'rev-parse', tag + '^{commit}'])
            return result

    def test_all_green(self):
        self.fixture()
        self.assertTrue(self.check().ok)

    def test_missing_version(self):
        self.fixture(); self.metadata['versions'] = {}
        self.assertFalse(self.check().ok)

    def test_not_latest(self):
        self.fixture(); self.metadata['dist-tags']['latest'] = '26.1125.0'
        self.assertFalse(self.check().ok)

    def test_missing_attestation(self):
        self.fixture(); self.metadata['versions']['26.1126.1']['dist'].pop('attestations')
        self.assertFalse(self.check().ok)

    def test_empty_attestation(self):
        self.fixture(); self.attestations['attestations'] = []
        self.assertFalse(self.check().ok)

    def test_wrong_sha(self):
        self.fixture({'tag': 'v26.11.26.1', 'npm': '26.1126.1', 'sha': 'b' * 40})
        self.assertFalse(self.check().ok)

    def test_wrong_embedded_tag(self):
        self.fixture({'tag': 'v26.11.25', 'npm': '26.1126.1', 'sha': 'a' * 40})
        self.assertFalse(self.check().ok)

    def test_tampered_tarball(self):
        self.fixture(); self.blob += b'bad'
        self.assertFalse(self.check().ok)

    def test_offline_failure(self):
        with patch.object(rv, '_download', side_effect=OSError('offline')):
            self.assertFalse(rv.fetch_npm_published('v26.11.26.1', self.root).ok)

    def test_historical_skip_has_no_io(self):
        with patch.object(rv, '_download') as download:
            self.assertTrue(rv.fetch_npm_published('v0.16.7', self.root).ok)
            self.assertTrue(rv.fetch_npm_published('v26.11.25.1', self.root).ok)
            download.assert_not_called()

    def test_consumer_semver_is_not_applicable_and_ok_without_registry_io(self):
        (self.root / 'launcher/package.json').unlink()
        with patch.object(rv, '_download') as download:
            for tag in ['v1.2.3', 'v2.0.0', 'v27.1.1']:
                result = rv.fetch_npm_published(tag, self.root)
                self.assertTrue(result.not_applicable)
                verified = rv.verify_release(tag, True, [rv.WorkflowRun('completed', 'success', 'url')],
                                             [rv.ReleaseRow(tag, False, True)], result)
                self.assertTrue(verified.ok)
                self.assertTrue(verified.to_dict()['npm_published']['not_applicable'])
            download.assert_not_called()

    def test_wrong_launcher_name_is_not_applicable(self):
        (self.root / 'launcher/package.json').write_text('{"name":"consumer"}')
        with patch.object(rv, '_download') as download:
            self.assertTrue(rv.fetch_npm_published('v27.1.1', self.root).not_applicable)
            download.assert_not_called()

    def test_marc_pre_npm_floor_is_not_applicable(self):
        with patch.object(rv, '_download') as download:
            for tag in ['v26.11.25', 'v26.11.25.1']:
                result = rv.fetch_npm_published(tag, self.root)
                self.assertTrue(result.not_applicable)
                self.assertTrue(rv.verify_release(tag, True, [rv.WorkflowRun('completed', 'success', 'url')],
                                                 [rv.ReleaseRow(tag, False, True)], result).ok)
            download.assert_not_called()

    def test_bootstrap_exception_only_skips_attestation(self):
        self.fixture({'tag': 'v26.11.25.2', 'npm': '26.1125.2', 'sha': 'a' * 40})
        package = self.metadata['versions'].pop('26.1126.1')
        package['version'] = '26.1125.2'
        package['dist'].pop('attestations')
        self.metadata['versions']['26.1125.2'] = package
        self.metadata['dist-tags']['latest'] = '26.1125.2'
        self.assertEqual(rv.BOOTSTRAP_UNATTESTED_TAG, 'v26.11.25.2')
        self.assertTrue(self.check('v26.11.25.2').ok)
        self.blob += b'tampered'
        self.assertFalse(self.check('v26.11.25.2').ok)

    def test_map_edges(self):
        for tag, npm in [('v27.1.1', '27.101.0'), ('v27.12.31', '27.1231.0'), ('v26.11.25.99', '26.1125.99')]:
            self.assertEqual(rv.mapped_npm_version(tag), npm)
        for tag in ['v26.11.24', 'v27.02.1', 'v27.2.29', 'v26.11.25.01']:
            with self.assertRaises(ValueError):
                rv.mapped_npm_version(tag)

    def test_modern_release_requires_npm(self):
        runs = [rv.WorkflowRun('completed', 'success', 'url')]
        releases = [rv.ReleaseRow('v26.11.26.1', False, True)]
        result = rv.verify_release('26.11.26.1', True, runs, releases, npm_applicable=True)
        self.assertFalse(result.ok)
        result = rv.verify_release('26.11.26.1', True, runs, releases, rv.CheckResult(True, 'fixture'))
        self.assertTrue(result.ok)
        self.assertIn('npm_published', result.to_dict())
        self.assertIn('npm_published', rv.render_human(result))


if __name__ == '__main__':
    unittest.main()
