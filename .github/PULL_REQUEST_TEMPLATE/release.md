## Release

Merging a version bump to `main` cuts a release automatically. To batch several
PRs into one daily version, bump the version only in the last PR.
Version/tag and editable release date:

- [ ] Bump all four harness manifests to the same version; update CHANGELOG.
- [ ] Move all three catalog pointers to `v` + that version:
  `.claude-plugin/marketplace.json`, `.github/plugin/marketplace.json`,
  `.agents/plugins/marketplace.json` (Copilot uses github; others git-subdir).
- [ ] Update launcher/package.json mapped npm version and release.json tag/npm;
  leave source SHA null (`release.yml` embeds the actual tag commit SHA).
- [ ] Run catalog parity, launcher tests, compilation check, and green CI.
- [ ] Independent `marc:sec review` and `marc:rev review` cover current HEAD.
- [ ] Dogfood all four real CLIs through terminal success, preserving scopes
  and disabled state. Record commands, versions and results here.
- [ ] Operator prerequisites: `v*` tag ruleset permits only the GitHub Actions
  integration to create tags and protects against update/delete; a rule blocks
  creation of `refs/heads/v*` branches. Humans and agents never create `v*` tags.
- [ ] `npm-publish` environment allows only `main`, with no required reviewer;
  npm trusted publisher binds `NexaDuo/mARC`, `release.yml`, `npm-publish`.
- [ ] Bootstrap only: follow the decision record's [bootstrap section](../../docs/marc/2026-11-25-decision-npx-launcher.md#bootstrap).
  The first publish job fails by design; publish once with a short-lived token,
  configure the trusted publisher, revoke the token, then dispatch on `main`.
- [ ] SEC-8: the merge-to-tag missing-ref window is a minute or two in a healthy run;
  confirm each CLI fails closed during that window. Scheduling delays or failed
  tests can extend it; preserve the concrete dogfood result.
- [ ] After merge, monitor `release.yml` to terminal completion and verify with
  `python3 core/scripts/release_verify.py`, including `npm_published` when
  applicable. Consumer repos and older tags report `not_applicable`.
