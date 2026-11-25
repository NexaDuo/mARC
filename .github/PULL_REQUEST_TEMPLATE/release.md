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
- [ ] Maintainer prerequisites before the first tag push: `v*` tag ruleset
  restricts create/update/delete to maintainers; a rule blocks creation of
  `refs/heads/v*` branches; `npm-publish` environment has required reviewers.
- [ ] Verify action SHA pins and npm trusted publisher bound to
  `NexaDuo/mARC`, workflow `npm-publish.yml`, environment `npm-publish`.
- [ ] Bootstrap only: manually publish `v26.11.26` / npm `26.1126.0` once
  from its clean tag with a short-lived token; revoke it after publisher setup.
  That exact version alone is exempt from the `npm_published` attestation
  check; integrity, latest, embedded version and peeled-tag SHA must still pass.
- [ ] Push the tag immediately after merge; confirm each CLI's missing-ref
  failure mode once and record the result (offline fixtures cannot prove it).
- [ ] After tagging, release_verify.py passes, including npm_published when
  applicable (matching launcher package, tags >= `v26.11.26`); consumer repos
  and older tags report `not_applicable`.
