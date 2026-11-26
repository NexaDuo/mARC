import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_doc_links as c  # noqa: E402


class DocLinks(unittest.TestCase):
    def test_slug_rules(self):
        self.assertEqual(c.slug('Declarative per-repo install (Claude Code)'), 'declarative-per-repo-install-claude-code')
        self.assertEqual(c.slug('OpenAI Codex'), 'openai-codex')

    def test_repo_links_resolve(self):
        self.assertEqual(c.check(), [])

    def test_new_doc_anchors_exist(self):
        wanted = {'teams.md': {'coordinate-several-teams'}, 'harnesses.md': {'openai-codex'},
                  'releases.md': {'release-on-merge'}, 'ARCHITECTURE.md': {'repository-layout'},
                  'install.md': {'declarative-per-repo-install-claude-code'}}
        for name, slugs in wanted.items():
            self.assertTrue(slugs <= c.anchors(c.ROOT / 'docs' / name), name)


if __name__ == '__main__':
    unittest.main()
