# Decision record: release-bound npx launcher for four harnesses

- **Type:** decision record
- **Date:** 2026-11-25
- **Attribution:** `@arch` decision and `@dev` P1–P4 probes on
  [#424](https://github.com/NexaDuo/mARC/issues/424), against `3000866`;
  user selected the npx channel.
- **Status:** accepted; implementation pending independent review and release

## Context

Four harnesses need a convenient way to stay on the same released version.
Since #419, old claim parsers can cause double claims across harnesses.
Antigravity still needs a local plugin directory. The launcher delegates to
native CLIs and never writes harness configuration itself. This partially
supersedes [#161's distribution decision](2026-07-24-decision-distribution-channel-marketplace-only.md)
only for the self-owned npx option.

## Options and decision

Use one npm package per release, rather than independent semver with runtime
tag resolution. `vYY.M.D[.N]` maps to `YY.(M*100+D).N`, defaulting N to zero:
`v26.11.26` becomes `26.1126.0`. Mapping rejects tags below `v26.11.25`, old
`v0.x` tags, leading zeros, and invalid calendar dates. Package versions have
exactly three components; calendar tags may have a fourth release component.
The floor ensures all installed claim parsers understand `marc:` markers.

Use release pointers inside unpinned, main-tracking marketplace catalogs.
A stable branch would require destructive marketplace re-registration, while
main-tracking plugin sources could install unreleased content under an old
version. A release PR moves all pointers and all four manifest versions
atomically. Pointers use tags, because a commit cannot embed its own SHA.
Installs fail closed between merging the release PR and automation creating the tag.

## Probes and native flows

P1–P4 passed on 2026-11-25 in isolated, deleted config directories: Claude
2.1.322, Copilot 1.0.89-1, Codex 0.167.1, agy 1.5.9. Probes used annotated
`v26.11.25` and `v26.11.25.1`. They refine the architecture as follows:

| Harness | Catalog source | Update |
|---|---|---|
| Claude | `git-subdir`, `url: NexaDuo/mARC`, harness path, release `ref` | `marketplace update nexaduo`, then `plugin update marc@nexaduo --scope <s>` for every installed scope |
| Copilot | `github`, `repo: NexaDuo/mARC`, harness path, release `ref` | `marketplace update nexaduo`, then `plugin update marc` |
| Codex | `git-subdir`, `url: NexaDuo/mARC`, harness path, release `ref` | `marketplace upgrade nexaduo`, then `plugin add marc@nexaduo` |
| Antigravity | no marketplace | shallow tag clone, verify HEAD against embedded SHA, install harness subdirectory, cleanup in `finally` |

Copilot rejects `git-subdir`. Its remote pointers require a git-hosted
catalog; local-directory catalogs are live sources, unsuitable for probing
copy-on-install. Codex re-adding an existing marketplace with another
`--ref` fails; new installations add `NexaDuo/mARC` without a ref and let the
catalog pointer govern. Install lists marketplaces first, skips add only for
an expected unpinned registration, and preserves conflicting/local/pinned
registrations with exact manual re-registration commands. `doctor` checks
both the marketplace origin and the plugin pointer's repo, path and tag.
Each harness reports success/failure independently; failures do not abort
later harnesses. The launcher never runs marketplace removal, including on Codex.
Antigravity copies its plugin and supports replacement. Reinstall preserves
disabled state; enable only on a fresh install or when already enabled.
The real GitHub-hosted catalog remains an end-to-end dogfood gate after landing.

## Launcher boundary

Top-level `launcher/` is independent of prompt compilation: zero dependencies,
no lifecycle scripts, Node >=20, `marc` binary, and a package file allowlist.
Commands: `install`, `update`, `doctor`, `uninstall`; repeatable `--harness`,
Claude-only `--scope`, `--yes`, `--dry-run`. Non-TTY mutations require `--yes`;
`doctor` and dry-run need no confirmation. Claude update dry-run reads the same
native installed-scope list as a real update and names every selected settings
path before consent. Deleted project paths warn, skip that scope and leave a
non-zero final status while valid scopes and other harnesses continue. `doctor` reads native JSON lists for versions and reads cached catalogs for
release pointers, reports drift and legacy pins, and exits 1 on drift or an
unverifiable catalog. Copilot cache lookup follows the locally probed native
checkout layout; a changed layout fails closed until supported. Uninstall only removes plugins. `--version` accepts the embedded
tag; another supported tag prints its mapped npx command and exits.

Launch with `spawn(file, argv)`, without a shell. Resolve absolute executables
from PATH/PATHEXT outside cwd; reject relative PATH entries and symlinks into
cwd. On Windows only known executable extensions qualify. Batch launch uses
system `cmd.exe /d /s /c`, never COMSPEC, with a strict argument allowlist and
constant-only options. User values retain the strict allowlist. Only internal
clone/install argv positions are marked as generated paths: absolute paths
may contain `~`, backslashes and colons but reject cmd metacharacters
(`& | < > ^ % ! " ( )`, CR/LF) and whitespace. Temp roots are expanded with
`fs.realpathSync.native` (including 8.3 names), preferring an existing writable
root without spaces. A generated batch path containing spaces fails with a
`TMP`/`TEMP` remedy; the launcher never quotes it into acceptance. Git's environment/config is isolated;
verify SHA before checkout, reject symlinks/submodules, check the manifest,
and always remove the temporary clone.

## Release-on-merge (user decision 2026-11-25)

Merging a version bump to `main` is the release action. To batch multiple PRs
into one daily version, bump only in the last PR. Humans and agents no longer
create `v*` tags; only GitHub Actions does. This supersedes the original
manual-tag publication design and closes the tag half of `marc:sec` SEC-9.

One SHA-pinned workflow, `.github/workflows/release.yml`, runs on `push` to
`main` or input-free `workflow_dispatch` on `main`, never on tags or PRs.
`concurrency: release` with cancellation disabled serializes active releases.
A tag pushed with `GITHUB_TOKEN` cannot trigger another workflow, so the
release and trusted publication jobs must live together:

- `plan` (contents read) reads the main event commit's manifest, maps tag/npm,
  checks origin, GitHub Releases and npm, and outputs what remains to do.
  npm is required only from `v26.11.26`. Existing tags never move: a push
  targeting a different commit fails loudly. Dispatch reuses the existing
  tag's commit, or the main event SHA if no tag exists. Targets must be
  ancestors of `origin/main`. Registry/API errors other than 404 fail closed.
- `test` (contents read) checks out the target commit and runs launcher tests,
  catalog parity, and tag/manifest/package/ancestry assertions when any work
  remains. A failure prevents tagging and both publication jobs.
- `tag` (contents write) creates an annotated tag on the target SHA using
  `github-actions[bot]`, checks again that the remote tag is absent, and
  pushes without force using `GITHUB_TOKEN`. Only this checkout persists
  credentials. A competing creator causes failure rather than replacement.
- `github-release` (contents write) uses CHANGELOG and its section extractor
  from the default branch, passes notes only through `--notes-file`, and
  creates or updates idempotently when the release is missing at planning.
  It also runs when the tag already existed and the tag job was skipped.
- `publish` (contents read plus id-token write) uses environment `npm-publish`,
  checks out the tag, reasserts HEAD equals its peeled commit and the planned
  SHA, ancestry and parity, embeds `{tag, sha, npm}` in `launcher/release.json`,
  and runs `npm publish --access public --provenance` only if npm is missing.
  Tests never execute with OIDC permission. No npm token lives in GitHub.

Source `release.json.sha` stays null; Antigravity fails closed until the
publish job embeds the real commit. Actions use pinned checkout v5.1.0 and
setup-node v5.0.0. SEC-8's merge-to-tag missing-ref window is now seconds in
a healthy run; scheduling or test failures can extend it. Confirm actual
CLI failure behavior during dogfood; offline tests do not prove it.

The operator configures these server-side prerequisites:

- The `v*` tag ruleset allows **only the GitHub Actions integration** to create
  tags and protects tags against update/deletion. Human and agent tokens
  cannot create them, including the maintainer's normal token.
- Block creation of `refs/heads/v*` branches to prevent branch/tag shadowing.
- Restrict environment `npm-publish` to the **main branch**, with **no required
  reviewer**; the workflow executes on main even when publish checks out a tag.
- Bind npm trusted publishing to `NexaDuo/mARC`, workflow **release.yml** and
  environment **npm-publish**. After bootstrap, require 2FA and disallow tokens.

These settings are external prerequisites; this patch does not configure them.
Residual risk: anyone who can merge to main can release. `marc:sec` SEC-9
identifies the merge gate and a non-admin agent token as the remaining controls.
No manual approval or maintainer tag push remains on the normal release path.

### Bootstrap

The first run's `publish` job fails by design when the npm package does not
yet exist: trusted publishing cannot create the initial package. It prints
a pointer to this section. OIDC refusal also fails with that pointer.
The maintainer publishes the first real launcher version once from a clean
checkout of the **Actions-created tag**, generating `release.json` with
`RELEASE_TAG`, `RELEASE_SHA` and `REQUIRE_RELEASE_TAG=true` passed to
`scripts/prepare_npm_release.sh`, then publishes with a short-lived granular
token kept outside the repository and never in GitHub secrets. Configure the
trusted publisher above and revoke the bootstrap token. Re-run via
`workflow_dispatch` on main or wait for the next release. Dispatch skips
surfaces that already exist; if the exact bootstrap version was manually
published, its npm job correctly skips too.

Only bootstrap tag `v26.11.26` (npm `26.1126.0`) is exempt from attestation,
through `BOOTSTRAP_UNATTESTED_TAG`; integrity, latest, embedded version and
peeled-tag SHA checks still apply. Every later version requires provenance.
The npm org `nexaduo` ownership was verified by the operator. npm provenance
helps only when someone verifies signatures; npx does not verify it automatically.

Two quick merges are serialized while running, but Actions may replace a
pending run with a newer one. A surviving run tags its own event SHA. If a
later push keeps an already-tagged version at another commit, planning fails
rather than moving the tag; use dispatch to retry that release. If tests fail
after merge, no tag or publication is created: repair through a reviewed PR
and dispatch on main. A skipped pending version can be recovered by re-running
its original push run; do not create its tag manually.

`release_verify.py` applies `npm_published` only when the repo has
`launcher/package.json` named `@nexaduo/marc` and the tag is at least `v26.11.26`.
Consumer repos and earlier mARC tags report `not_applicable` without registry
I/O and do not count against overall `ok`. Applicable checks require the mapped
npm version, latest dist-tag, provenance presence (with the exact bootstrap
exception above), tarball integrity, and an embedded SHA matching the locally
peeled tag commit. It does not cryptographically verify the attestation.

## Validation and release gates

Node's offline tests cover version mapping, exact native argv, scopes, no-write,
no marketplace removal, no shell, non-TTY/dry-run, Windows batch validation,
Antigravity failure cleanup/state, list fixtures and package contents. Linux
and Windows CI run these tests. A schema-aware parity gate checks all three
catalogs against all four manifests and the launcher's package version,
release tag/npm and null source SHA. Token benchmarks also prepare temporary
local-source catalogs from each arm's checkout, including older baselines. Native CI installation uses temporary
copies with local relative sources to test the PR tree. Mocked Python tests
cover npm verification without network.

Before release: independent security/review passes, green CI, complete action
pins, tag ruleset, npm trusted publisher bootstrap, and real-CLI dogfood across
all four harnesses through terminal success. Automated local/stub tests cannot
replace that manual matrix. The release date is editable in the changelog.
