# Releases

How a mARC release happens: one `VERSION` file, stamped into every copy.

Back to the [README](../README.md).

## Release on merge

Merging a change to `VERSION` on `main` releases mARC automatically through
`release.yml`: tests, an Actions-created annotated tag, GitHub Release notes,
and trusted npm publishing. Humans and agents do not create `v*` tags. To batch
several PRs into one daily version, bump only in the last PR. To release: edit
`VERSION` and `CHANGELOG.md`, run `python3 scripts/bump_version.py <ver>` (it stamps
the manifests, catalog pointers and launcher files), and open the PR; CI runs
`bump_version.py --check`. After merge, verify
with `python3 core/scripts/release_verify.py`; retry missing surfaces with
`workflow_dispatch` on `main`. See the [release decision and bootstrap](marc/2026-11-25-decision-npx-launcher.md#release-on-merge-user-decision-2026-11-25).

## Rollback

Rolling back to an earlier mARC version is not supported. Per-repo config now has
one source of truth, `.agents/team.toml` (#166). Keeping a readable copy at the
legacy path so an older version could still find it would recreate the
two-live-configs split-brain that change removed, so downgrade safety was
deliberately not chosen (#174). Versions before 0.22.0 do not look in
`.agents/`: they report "no `team.toml` found" and fall back to runtime `gh`
discovery, and re-running their `/marc:init` writes a second, legacy
`team.toml`.

Instead, update forward with `claude plugin update marc@nexaduo`. If you must
stay on an older version, copy `.agents/team.toml` back to the harness's legacy
config path.
