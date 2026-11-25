#!/usr/bin/env python3
"""Offline npm release verification fixtures (#424), no network or real git."""
import base64
import hashlib
import io
import json
import sys
import tarfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release_verify as rv


class NpmPublished(unittest.TestCase):
    def fixture(self, release=None):
        release = release or {'tag': 'v26.11.26', 'npm': '26.1126.0', 'sha': 'a' * 40}
        content = json.dumps(release).encode()
        out = io.BytesIO()
        with tarfile.open(fileobj=out, mode='w:gz') as archive:
            member = tarfile.TarInfo('package/release.json')
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
        blob = out.getvalue()
        self.metadata = {'dist-tags': {'latest': '26.1126.0'}, 'versions': {'26.1126.0': {
            'version': '26.1126.0', 'dist': {
                'tarball': 'https://registry.npmjs.org/marc.tgz',
                'integrity': 'sha512-' + base64.b64encode(hashlib.sha512(blob).digest()).decode(),
                'attestations': {'url': 'https://registry.npmjs.org/attestations'}}}}}
        self.attestations = {'attestations': [{'predicateType': 'https://slsa.dev/provenance/v1', 'bundle': {'content': 'fixture'}}]}
        self.blob = blob

    def check(self):
        def download(url, limit):
            if url.endswith('.tgz'):
                return self.blob
            return json.dumps(self.attestations if url.endswith('attestations') else self.metadata).encode()
        with patch.object(rv, '_download', side_effect=download), patch.object(rv, '_run', return_value='a' * 40 + '\n') as run:
            result = rv.fetch_npm_published('v26.11.26', Path('/repo'))
            if result.ok:
                run.assert_called_once_with(['git', '-C', '/repo', 'rev-parse', 'v26.11.26^{commit}'])
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
        self.fixture(); self.metadata['versions']['26.1126.0']['dist'].pop('attestations')
        self.assertFalse(self.check().ok)

    def test_empty_attestation(self):
        self.fixture(); self.attestations['attestations'] = []
        self.assertFalse(self.check().ok)

    def test_wrong_sha(self):
        self.fixture({'tag': 'v26.11.26', 'npm': '26.1126.0', 'sha': 'b' * 40})
        self.assertFalse(self.check().ok)

    def test_wrong_embedded_tag(self):
        self.fixture({'tag': 'v26.11.25', 'npm': '26.1126.0', 'sha': 'a' * 40})
        self.assertFalse(self.check().ok)

    def test_tampered_tarball(self):
        self.fixture(); self.blob += b'bad'
        self.assertFalse(self.check().ok)

    def test_offline_failure(self):
        with patch.object(rv, '_download', side_effect=OSError('offline')):
            self.assertFalse(rv.fetch_npm_published('v26.11.26', Path('.')).ok)

    def test_historical_skip_has_no_io(self):
        with patch.object(rv, '_download') as download:
            self.assertTrue(rv.fetch_npm_published('v0.16.7', Path('.')).ok)
            self.assertTrue(rv.fetch_npm_published('v26.11.24', Path('.')).ok)
            download.assert_not_called()

    def test_map_edges(self):
        for tag, npm in [('v27.1.1', '27.101.0'), ('v27.12.31', '27.1231.0'), ('v26.11.25.99', '26.1125.99')]:
            self.assertEqual(rv.mapped_npm_version(tag), npm)
        for tag in ['v26.11.24', 'v27.02.1', 'v27.2.29', 'v26.11.25.01']:
            with self.assertRaises(ValueError):
                rv.mapped_npm_version(tag)

    def test_modern_release_requires_npm(self):
        runs = [rv.WorkflowRun('completed', 'success', 'url')]
        releases = [rv.ReleaseRow('v26.11.26', False, True)]
        result = rv.verify_release('26.11.26', True, runs, releases)
        self.assertFalse(result.ok)
        result = rv.verify_release('26.11.26', True, runs, releases, rv.CheckResult(True, 'fixture'))
        self.assertTrue(result.ok)
        self.assertIn('npm_published', result.to_dict())
        self.assertIn('npm_published', rv.render_human(result))


if __name__ == '__main__':
    unittest.main()
