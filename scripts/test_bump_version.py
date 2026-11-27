#!/usr/bin/env python3
"""Offline pointer parity and local PR-tree catalog regressions."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from bump_version import check, stamp, read_version, CATALOGS, tag_to_npm
from local_test_marketplace import prepare, ROOT


class CatalogPointers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='marc-catalog-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'launcher').mkdir()
        (self.root / 'VERSION').write_text('26.11.25.2\n')
        (self.root / 'launcher/package.json').write_text(json.dumps({'version': '26.1125.2'}))
        (self.root / 'launcher/release.json').write_text(json.dumps({'tag': 'v26.11.25.2', 'npm': '26.1125.2', 'sha': None}))
        for harness in (*CATALOGS, 'antigravity'):
            target = self.root / 'harnesses' / harness / 'marc' / ('.claude-plugin/plugin.json' if harness == 'claude-code' else 'plugin.json')
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'version': '26.11.25.2'}))
        for harness, catalog in CATALOGS.items():
            target = self.root / catalog
            target.parent.mkdir(parents=True)
            source = {'source': 'github', 'repo': 'NexaDuo/mARC'} if harness == 'copilot' else {'source': 'git-subdir', 'url': 'NexaDuo/mARC'}
            source.update(path=f'harnesses/{harness}/marc', ref='v26.11.25.2')
            target.write_text(json.dumps({'name': 'nexaduo', 'plugins': [{'name': 'marc', 'source': source}]}))

    def edit(self, catalog, edit):
        target = self.root / catalog
        data = json.loads(target.read_text()); edit(data)
        target.write_text(json.dumps(data))

    def test_positive(self):
        self.assertEqual(check(self.root), 'v26.11.25.2')

    def test_wrong_pointer(self):
        for catalog in CATALOGS.values():
            original = (self.root / catalog).read_text()
            self.edit(catalog, lambda d: d['plugins'][0]['source'].update(ref='main'))
            with self.assertRaises(ValueError): check(self.root)
            (self.root / catalog).write_text(original)

    def test_wrong_copilot_schema(self):
        self.edit(CATALOGS['copilot'], lambda d: d['plugins'][0]['source'].update(source='git-subdir'))
        with self.assertRaises(ValueError): check(self.root)

    def test_version_drift(self):
        (self.root / 'harnesses/antigravity/marc/plugin.json').write_text('{"version":"26.11.25"}')
        with self.assertRaisesRegex(ValueError, 'harnesses/antigravity/marc/plugin.json'): check(self.root)

    def test_version_file_drift(self):
        (self.root / 'VERSION').write_text('26.11.25.3\n')
        with self.assertRaisesRegex(ValueError, 'plugin.json'): check(self.root)

    def test_version_file_must_be_one_clean_line(self):
        for bad in ('26.11.25.2', '26.11.25.2\n\n', ' 26.11.25.2\n', 'v26.11.25.2\n', '26.11.24\n'):
            (self.root / 'VERSION').write_text(bad)
            with self.subTest(bad=bad), self.assertRaises(ValueError): read_version(self.root)

    def test_stamp_round_trip_then_mutation_names_the_file(self):
        stamp('26.12.1', self.root)
        self.assertEqual(check(self.root), 'v26.12.1')
        self.assertEqual(json.loads((self.root / 'launcher/package.json').read_text())['version'], '26.1201.0')
        self.assertEqual(json.loads((self.root / 'launcher/release.json').read_text()),
                         {'tag': 'v26.12.1', 'sha': None, 'npm': '26.1201.0'})
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        stamp('26.12.1', self.root)  # idempotent
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})
        for name in ['harnesses/codex/marc/plugin.json', CATALOGS['codex'], 'launcher/package.json', 'launcher/release.json', 'VERSION']:
            path = self.root / name
            original = path.read_text()
            path.write_text(original.replace('26.12.1', '26.12.2').replace('26.1201.0', '26.1202.0') if name != 'VERSION' else '26.12.2\n')
            with self.subTest(name=name), self.assertRaises(ValueError) as caught:
                check(self.root)
            if name != 'VERSION':
                self.assertIn(name, str(caught.exception))
            path.write_text(original)

    def test_failed_stamp_changes_no_file(self):
        (self.root / 'harnesses/codex/marc/plugin.json').unlink()
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaises(OSError): stamp('26.12.1', self.root)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def test_unstampable_target_changes_no_file(self):
        (self.root / 'launcher/package.json').write_text('{}')
        before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        with self.assertRaisesRegex(ValueError, 'package.json'): stamp('26.12.1', self.root)
        self.assertEqual(before, {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()})

    def hidden_temps(self):
        return [p for p in self.root.rglob('.*') if p.is_file()]

    def test_stamp_leaves_no_temp_files_and_mode_0644(self):
        stamp('26.12.1', self.root)
        self.assertEqual(self.hidden_temps(), [])
        if os.name == 'posix':
            for name in ('VERSION', 'launcher/package.json', 'launcher/release.json'):
                self.assertEqual((self.root / name).stat().st_mode & 0o777, 0o644)

    def test_failed_replace_propagates_and_removes_own_temp(self):
        with mock.patch('bump_version.os.replace', side_effect=OSError('boom')):
            with self.assertRaisesRegex(OSError, 'boom'): stamp('26.12.1', self.root)
        self.assertEqual(self.hidden_temps(), [])
        self.assertEqual((self.root / 'VERSION').read_text(), '26.11.25.2\n')

    def test_stamp_ignores_planted_tmp_symlink(self):
        outside = self.root / 'outside.txt'
        outside.write_text('precious')
        for name in ('VERSION', 'launcher/package.json'):
            planted = self.root / (name + '.tmp')
            try:
                planted.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest('symlinks unavailable')
        stamp('26.12.1', self.root)
        self.assertEqual(outside.read_text(), 'precious')
        for name in ('VERSION', 'launcher/package.json'):
            target = self.root / name
            self.assertTrue(target.is_file() and not target.is_symlink())
            planted = self.root / (name + '.tmp')
            self.assertTrue(planted.is_symlink())
            self.assertEqual(planted.resolve(), outside.resolve())
        self.assertEqual((self.root / 'VERSION').read_text(), '26.12.1\n')
        self.assertEqual(check(self.root), 'v26.12.1')

    def test_repo_is_in_parity(self):
        self.assertEqual(check(ROOT), 'v' + read_version(ROOT))

    def test_launcher_version_drift(self):
        self.edit('launcher/package.json', lambda d: d.update(version='26.1125.0'))
        with self.assertRaisesRegex(ValueError, 'launcher/package.json: version'): check(self.root)

    def test_launcher_identity_drift(self):
        for field, value in [('tag', 'v26.11.25'), ('npm', '26.1125.0'), ('sha', 'a' * 40)]:
            original = (self.root / 'launcher/release.json').read_text()
            self.edit('launcher/release.json', lambda d: d.update({field: value}))
            with self.assertRaisesRegex(ValueError, 'release.json'): check(self.root)
            (self.root / 'launcher/release.json').write_text(original)

    def test_local_catalog_can_prepare_a_historical_single_harness_arm(self):
        import shutil
        source = self.root / 'old-arm'
        source.mkdir()
        shutil.copytree(ROOT / 'harnesses/claude-code', source / 'harnesses/claude-code')
        catalog = source / CATALOGS['claude-code']
        catalog.parent.mkdir(parents=True)
        shutil.copyfile(ROOT / CATALOGS['claude-code'], catalog)
        destination = self.root / 'old-catalog'
        prepare(destination, source)
        data = json.loads((destination / CATALOGS['claude-code']).read_text())
        self.assertEqual(data['plugins'][0]['source'], './harnesses/claude-code/marc')
        self.assertFalse((destination / CATALOGS['codex']).exists())

    def test_python_mapping_matches_js_edges_and_rejections(self):
        # All explicit JS tag fixtures plus leap days, safe-integer boundary,
        # and end-anchor newlines. Compare both acceptance and mapped value.
        cases = ['v27.1.1', 'v27.12.31', 'v27.9.30', 'v27.10.1', 'v26.11.25.99',
                 'v0.9.0', 'v26.11.24', 'v26.01.1', 'v26.11.025', 'v26.11.25.2.01',
                 'v26.11.25.2.0', 'v27.2.29', 'v27.13.1', 'v26.11.25.2.1.2',
                 'v26.11.25.2\\n', 'v26.11.25.2\\r\\n', 'v28.2.29',
                 'v26.11.25.2.9007199254740991', 'v26.11.25.2.9007199254740992']
        cases = [s.replace('\\n', '\n').replace('\\r', '\r') for s in cases]
        js = "import {tagToNpm} from './launcher/lib/version.js'; console.log(JSON.stringify(JSON.parse(process.argv[1]).map(t => {try{return tagToNpm(t)}catch{return null}})));"
        result = subprocess.run(['node', '--input-type=module', '-e', js, json.dumps(cases)], cwd=ROOT, capture_output=True, text=True, check=True)
        for tag, expected in zip(cases, json.loads(result.stdout)):
            with self.subTest(tag=tag):
                if expected is None:
                    with self.assertRaises(ValueError): tag_to_npm(tag)
                else:
                    self.assertEqual(tag_to_npm(tag), expected)

    def test_local_sources_resolve_to_copied_pr_tree(self):
        dest = self.root / 'copy'
        prepare(dest, ROOT)
        for harness, catalog in CATALOGS.items():
            source = json.loads((dest / catalog).read_text())['plugins'][0]['source']
            local = source['path'] if isinstance(source, dict) else source
            if isinstance(source, dict): self.assertEqual(source['source'], 'local')
            plugin = dest / local
            self.assertTrue(plugin.is_dir())
            original = ROOT / 'harnesses' / harness / 'marc'
            self.assertEqual((plugin / 'skills/tech-lead/SKILL.md').read_bytes(), (original / 'skills/tech-lead/SKILL.md').read_bytes())
            self.assertNotEqual(plugin.resolve(), original.resolve())


if __name__ == '__main__':
    unittest.main()
