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
Installs fail closed between merging the release PR and pushing the tag.

## Probes and native flows

P1–P4 passed on 2026-11-25 in isolated, deleted config directories: Claude
2.1.322, Copilot 1.0.89-1, Codex 0.167.1, agy 1.5.9. Probes used annotated
`v26.11.25` and `v26.11.25.1`. They refine the architecture as follows:

| Harness | Catalog source | Update |
|---|---|---|
| Claude | `git-subdir`, `url: NexaDuo/mARC`, harness path, release `ref` | `marketplace update nexaduo`, then `plugin update marc@nexaduo --scope <s>` for every installed scope |
| Copilot | `github`, `repo: NexaDuo/mARC`, harness path, release `ref` | `marketplace update nexaduo`, then `plugin update marc` |
| Codex | `git-subdir`, `url: NexaDuo/mARC`, harness path, release `ref` | `marketplace upgrade`, then `plugin add marc@nexaduo` |
| Antigravity | no marketplace | shallow tag clone, verify HEAD against embedded SHA, install harness subdirectory, cleanup in `finally` |

Copilot rejects `git-subdir`. Its remote pointers require a git-hosted
catalog; local-directory catalogs are live sources, unsuitable for probing
copy-on-install. Codex re-adding an existing marketplace with another
`--ref` fails; new installations add `NexaDuo/mARC` without a ref and let the
catalog pointer govern. `doctor` reports legacy pins and gives manual
guidance. The launcher never runs marketplace removal, including on Codex.
Antigravity copies its plugin and supports replacement. Reinstall preserves
disabled state; enable only on a fresh install or when already enabled.
The real GitHub-hosted catalog remains an end-to-end dogfood gate after landing.

## Launcher boundary

Top-level `launcher/` is independent of prompt compilation: zero dependencies,
no lifecycle scripts, Node >=20, `marc` binary, and a package file allowlist.
Commands: `install`, `update`, `doctor`, `uninstall`; repeatable `--harness`,
Claude-only `--scope`, `--yes`, `--dry-run`. Non-TTY runs require `--yes`.
Dry-run starts no subprocess. Project/local plans identify Claude's settings
file. `doctor` reads native JSON lists for versions and reads cached catalogs for
release pointers, reports drift and legacy pins, and exits 1 on drift or an
unverifiable catalog. Copilot cache lookup follows the locally probed native
checkout layout; a changed layout fails closed until supported. Uninstall only removes plugins. `--version` accepts the embedded
tag; another supported tag prints its mapped npx command and exits.

Launch with `spawn(file, argv)`, without a shell. Resolve absolute executables
from PATH/PATHEXT outside cwd; reject relative PATH entries and symlinks into
cwd. On Windows only known executable extensions qualify. Batch launch uses
system `cmd.exe /d /s /c`, never COMSPEC, with a strict argument allowlist and
constant-only options. Batch arguments containing spaces or shell characters
fail closed, including temporary paths. Git's environment/config is isolated;
verify SHA before checkout, reject symlinks/submodules, check the manifest,
and always remove the temporary clone.

## Publication and verification

Keep `.github/workflows/npm-publish.yml` stable for npm trusted publishing.
It triggers only on tag pushes, checks out `github.ref`, asserts tag/manifests/
pointers/package parity, generates `release.json` with tag, GITHUB_SHA and npm
version, then publishes publicly with provenance. Permissions: contents read,
id-token write. No npm secret in GitHub. Source `release.json.sha` is null;
Antigravity fails closed until the publish workflow embeds the real commit.
All actions are SHA-pinned (`actions/checkout` v5.1.0, `actions/setup-node` v5.0.0).

Maintainer bootstrap: there is no placeholder. A maintainer publishes the first
real launcher version once, from a clean checkout of its tag, with `release.json`
generated as the workflow does and a short-lived granular token kept outside the
repository and never in GitHub secrets. That single version has no provenance.
The maintainer then configures the trusted publisher (`NexaDuo/mARC`,
`npm-publish.yml`), sets "require 2FA and disallow tokens" and revokes the token.
Every later version publishes from CI with provenance. A maintainer must
create a `v*` tag ruleset blocking update and deletion. Neither task is automated
by this PR. npm provenance helps only when someone verifies signatures; npx
does not verify it automatically.

`release_verify.py` requires the mapped npm version, latest dist-tag,
provenance presence, tarball integrity, and an embedded SHA matching the locally
peeled tag commit. It does not cryptographically verify the attestation.

## Validation and release gates

Node's offline tests cover version mapping, exact native argv, scopes, no-write,
no marketplace removal, no shell, non-TTY/dry-run, Windows batch validation,
Antigravity failure cleanup/state, list fixtures and package contents. Linux
and Windows CI run these tests. A schema-aware parity gate checks all three
catalogs against all four manifests. Native CI installation uses temporary
copies with local relative sources to test the PR tree. Mocked Python tests
cover npm verification without network.

Before release: independent security/review passes, green CI, complete action
pins, tag ruleset, npm trusted publisher bootstrap, and real-CLI dogfood across
all four harnesses through terminal success. Automated local/stub tests cannot
replace that manual matrix. The release date is editable in the changelog.
