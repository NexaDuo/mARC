## Release

Version/tag and editable release date:

- [ ] Bump all four harness manifests to the same version; update CHANGELOG.
- [ ] Move all three catalog pointers to `v` + that version:
  `.claude-plugin/marketplace.json`, `.github/plugin/marketplace.json`,
  `.agents/plugins/marketplace.json` (Copilot uses github; others git-subdir).
- [ ] Update launcher/package.json mapped npm version and release.json tag/npm;
  leave source SHA null (npm-publish.yml embeds the actual tag commit SHA).
- [ ] Run catalog parity, launcher tests, compilation check, and green CI.
- [ ] Independent `marc:sec review` and `marc:rev review` cover current HEAD.
- [ ] Dogfood all four real CLIs through terminal success, preserving scopes
  and disabled state. Record commands, versions and results here.
- [ ] Maintainer: verify v* tag update/delete protection, action SHA pins,
  and npm trusted publisher bootstrap before pushing the immutable tag.
- [ ] After tagging, release_verify.py passes, including npm_published.
