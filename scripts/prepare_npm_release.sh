#!/usr/bin/env bash
# Shared cheap publish gates; run in both the read-only test and OIDC jobs.
set -euo pipefail
: "${GITHUB_SHA:?}" "${GITHUB_REF_NAME:?}" "${GITHUB_REF:?}"
[[ "$GITHUB_REF" == "refs/tags/$GITHUB_REF_NAME" ]]
[[ "$(git rev-parse HEAD)" == "$GITHUB_SHA" ]] || { echo 'Checkout/event SHA mismatch' >&2; exit 1; }
git fetch --no-tags origin refs/heads/main:refs/remotes/origin/main
git merge-base --is-ancestor "$GITHUB_SHA" origin/main || { echo 'Release commit is not an ancestor of origin/main' >&2; exit 1; }
python3 scripts/check_catalog_pointers.py
node --input-type=module <<'JS'
import fs from 'node:fs';
import { tagToNpm } from './launcher/lib/version.js';
const tag = process.env.GITHUB_REF_NAME;
const manifest = JSON.parse(fs.readFileSync('harnesses/claude-code/marc/.claude-plugin/plugin.json'));
if (tag !== `v${manifest.version}`) throw new Error('Tag/manifest mismatch');
if (!/^[a-f0-9]{40}$/.test(process.env.GITHUB_SHA)) throw new Error('Invalid release SHA');
const npm = tagToNpm(tag);
const pkg = JSON.parse(fs.readFileSync('launcher/package.json'));
if (pkg.version !== npm) throw new Error('Package/tag version mismatch');
fs.writeFileSync('launcher/release.json', JSON.stringify({ tag, sha: process.env.GITHUB_SHA, npm }, null, 2) + '\n');
JS
