#!/usr/bin/env python3
"""Offline pointer parity and local PR-tree catalog regressions."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_catalog_pointers import check, CATALOGS
from local_test_marketplace import prepare, ROOT


class CatalogPointers(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='marc-catalog-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for harness in (*CATALOGS, 'antigravity'):
            target = self.root / 'harnesses' / harness / 'marc' / ('.claude-plugin/plugin.json' if harness == 'claude-code' else 'plugin.json')
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps({'version': '26.11.26'}))
        for harness, catalog in CATALOGS.items():
            target = self.root / catalog
            target.parent.mkdir(parents=True)
            source = {'source': 'github', 'repo': 'NexaDuo/mARC'} if harness == 'copilot' else {'source': 'git-subdir', 'url': 'NexaDuo/mARC'}
            source.update(path=f'harnesses/{harness}/marc', ref='v26.11.26')
            target.write_text(json.dumps({'name': 'nexaduo', 'plugins': [{'name': 'marc', 'source': source}]}))

    def edit(self, catalog, edit):
        target = self.root / catalog
        data = json.loads(target.read_text()); edit(data)
        target.write_text(json.dumps(data))

    def test_positive(self):
        self.assertEqual(check(self.root), 'v26.11.26')

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
        with self.assertRaises(ValueError): check(self.root)

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
