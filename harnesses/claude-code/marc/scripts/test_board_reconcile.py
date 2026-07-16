#!/usr/bin/env python3
"""Self-test for `board_reconcile.py`'s normalized `--json` output contract
(origin: #103).

Stdlib only (no pytest); run directly:  python3 test_board_reconcile.py

This is NOT an E2E test (no real repo, no network, no `gh`/`git` calls) —
board reconciliation is internal operator tooling, so the regression coverage
that matters is the CONTRACT: feed `GitHubProvider` canned fixture data (via
its injectable `run` callable) and assert the resulting digest keeps the exact
normalized shape downstream tooling (the skill, a future provider, a human
operator) relies on. A silent schema drift here would break every consumer at
once without a loud test failure, so this file is the guardrail. A future
Azure DevOps / Jira provider is held to the SAME contract asserted below.

Covers:
  * top-level digest keys + `schema_version` (the drift guard itself);
  * `items[]` shape (id/title/status/assignee/linked_pr) from a fixture with
    both a board-configured project and the zero-config open-issues fallback;
  * `recent_merges[]` shape;
  * `release` — manifest/tag/release version match detection (match + mismatch
    fixtures);
  * `main_drift` — in-sync vs ahead/behind fixtures;
  * graceful degradation — a failing `gh`/`git` call never raises, always
    surfaces a `warnings` entry and a digest that still validates the schema;
  * `toml_get` zero-dependency team.toml extraction (mirrors the skill's shell
    `sed` pattern) including the `[board].provider` key.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from board_reconcile import (  # noqa: E402
    GitHubProvider,
    RepoConfig,
    SCHEMA_VERSION,
    build_digest,
    toml_get,
)

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("PASS" if cond else "FAIL") + f": {msg}")
    if not cond:
        _failures.append(msg)


EXPECTED_TOP_KEYS = {
    "schema_version", "provider", "repo", "board_configured", "board_number",
    "board_title", "items", "recent_merges", "release", "main_drift", "warnings",
}
EXPECTED_ITEM_KEYS = {"id", "title", "status", "assignee", "linked_pr"}
EXPECTED_MERGE_KEYS = {"number", "title", "merged_at", "url"}
EXPECTED_RELEASE_KEYS = {"manifest_version", "latest_tag", "latest_release_tag", "match", "notes"}
EXPECTED_DRIFT_KEYS = {"local_sha", "remote_sha", "ahead", "behind", "in_sync", "notes"}


def make_fake_run(responses: dict):
    """Returns a `run` callable matching a command's first two tokens (e.g.
    ("gh", "project") or ("git", "fetch")) to a canned stdout string, or
    raises RuntimeError to simulate a real-world failure."""

    def _run(cmd: list) -> str:
        key = tuple(cmd[:2])
        if key not in responses:
            raise RuntimeError(f"no fixture for command: {cmd}")
        val = responses[key]
        if isinstance(val, Exception):
            raise val
        return val

    return _run


def project_item_list_fixture() -> str:
    return json.dumps({
        "items": [
            {
                "id": "PVTI_abc123",
                "status": "In Progress",
                "content": {"number": 103, "title": "bundle board_reconcile.py", "url": "https://x/103"},
            },
            {
                "id": "PVTI_def456",
                "status": "Todo",
                "content": {"number": 104, "title": "some other item", "url": "https://x/104"},
            },
        ]
    })


def graphql_linked_pr_fixture(pr_number: int) -> str:
    return json.dumps({
        "data": {"repository": {"issue": {
            "closedByPullRequestsReferences": {"nodes": [{"number": pr_number}]}
        }}}
    })


def graphql_no_linked_pr_fixture() -> str:
    return json.dumps({
        "data": {"repository": {"issue": {
            "closedByPullRequestsReferences": {"nodes": []}
        }}}
    })


def pr_list_merged_fixture() -> str:
    return json.dumps([
        {"number": 99, "title": "chore(release): bump", "mergedAt": "2026-07-16T00:17:01Z",
         "url": "https://x/pr/99"},
    ])


def validate_digest_schema(d: dict, label: str) -> None:
    check(set(d.keys()) == EXPECTED_TOP_KEYS, f"[{label}] top-level keys match the normalized contract")
    check(d.get("schema_version") == SCHEMA_VERSION, f"[{label}] schema_version == {SCHEMA_VERSION}")
    check(isinstance(d.get("items"), list), f"[{label}] items is a list")
    for it in d.get("items", []):
        check(set(it.keys()) == EXPECTED_ITEM_KEYS, f"[{label}] item keys match contract: {it}")
    for m in d.get("recent_merges", []):
        check(set(m.keys()) == EXPECTED_MERGE_KEYS, f"[{label}] recent_merge keys match contract: {m}")
    rel = d.get("release") or {}
    check(set(rel.keys()) == EXPECTED_RELEASE_KEYS, f"[{label}] release keys match contract: {rel}")
    drift = d.get("main_drift") or {}
    check(set(drift.keys()) == EXPECTED_DRIFT_KEYS, f"[{label}] main_drift keys match contract: {drift}")
    check(isinstance(d.get("warnings"), list), f"[{label}] warnings is a list")


def test_board_configured_happy_path():
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2,
                      project_title="mARC's project", provider="github")
    responses = {
        ("gh", "project"): project_item_list_fixture(),
        ("gh", "api"): graphql_linked_pr_fixture(97),
        ("gh", "pr"): pr_list_merged_fixture(),
        ("git", "describe"): "v0.16.2\n",
        ("gh", "release"): json.dumps({"tagName": "v0.16.2"}),
        ("git", "-C"): "",  # fetch / rev-parse / rev-list all hit this key; see override below
    }
    provider = GitHubProvider(cfg, run=make_fake_run(responses))

    # git -C needs per-subcommand branching (fetch / rev-parse main / rev-parse
    # origin/main / rev-list) so give it a dedicated stateful fixture.
    calls = {"n": 0}

    def git_dash_c(cmd: list) -> str:
        if "fetch" in cmd:
            return ""
        if cmd[-1] == "main" and "rev-parse" in cmd:
            return "aaaa000\n"
        if cmd[-1] == "origin/main" and "rev-parse" in cmd:
            return "bbbb111\n"
        if "rev-list" in cmd:
            return "2\t0\n"  # behind, ahead
        raise RuntimeError(f"unhandled git -C fixture: {cmd}")

    def run(cmd: list) -> str:
        if cmd[:2] == ["git", "-C"]:
            return git_dash_c(cmd)
        return make_fake_run(responses)(cmd)

    provider._run = run  # noqa: SLF001 - test-only override, injectable by design

    manifest_dir = tempfile.mkdtemp()
    manifest_path = os.path.join(manifest_dir, "plugin.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump({"version": "0.16.2"}, f)

    from pathlib import Path
    release = provider.get_release_state(Path(manifest_path))
    check(release.match is True, "release: manifest version matches latest tag/release -> match=True")

    mismatched_manifest = os.path.join(manifest_dir, "plugin_old.json")
    with open(mismatched_manifest, "w", encoding="utf-8") as f:
        json.dump({"version": "0.15.0"}, f)
    release_mismatch = provider.get_release_state(Path(mismatched_manifest))
    check(release_mismatch.match is False, "release: mismatched manifest version -> match=False")

    drift = provider.get_main_drift(Path(manifest_dir))
    check(drift.ahead == 0 and drift.behind == 2, "main_drift: ahead/behind parsed from rev-list fixture")
    check(drift.in_sync is False, "main_drift: ahead/behind nonzero -> in_sync=False")

    items, board_configured, board_number, board_title, warnings = provider.list_open_items(50)
    check(board_configured is True, "board_configured True when project_number+gh_org set")
    check(board_number == 2, "board_number echoes team.toml project_number")
    check(len(items) == 2, "list_open_items returns both fixture items")
    check(items[0].status == "In Progress", "item status comes from the project's Status field")
    check(items[0].linked_pr == 97, "linked_pr resolved via closedByPullRequestsReferences fixture")

    digest = build_digest(cfg, Path(manifest_dir), open_limit=50, merges_limit=10, provider=provider)
    validate_digest_schema(digest.to_dict(), "board-configured happy path (assembled via build_digest, fixture run())")


def test_zero_config_fallback():
    """No project_number in team.toml -> falls back to open issues, still
    conforms to the exact same normalized shape (the provider-agnostic
    contract a future provider must also satisfy)."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=None, provider="github")

    def run(cmd: list) -> str:
        if cmd[:3] == ["gh", "issue", "list"]:
            return json.dumps([
                {"number": 1, "title": "untracked issue", "assignees": [{"login": "octocat"}]},
                {"number": 2, "title": "unassigned issue", "assignees": []},
            ])
        if cmd[:3] == ["gh", "pr", "list"]:
            raise RuntimeError("simulated: no auth")
        if cmd[:2] == ["git", "describe"]:
            raise RuntimeError("simulated: no tags")
        if cmd[:2] == ["gh", "release"]:
            raise RuntimeError("simulated: no releases")
        if cmd[:2] == ["git", "-C"] and "fetch" in cmd:
            raise RuntimeError("simulated: offline")
        raise RuntimeError(f"unhandled fixture: {cmd}")

    from pathlib import Path
    provider = GitHubProvider(cfg, run=run)
    items, board_configured, _, _, warnings = provider.list_open_items(50)
    check(board_configured is False, "no project_number -> board_configured=False")
    check(any("no board configured" in w for w in warnings), "degradation warning present for missing board")
    check(len(items) == 2, "zero-config fallback still returns normalized items")
    check(items[0].assignee == "octocat", "zero-config item carries an assignee when present")
    check(items[1].assignee is None, "zero-config item assignee is None when absent")

    merges, merge_warnings = provider.list_recent_merges(10)
    check(merges == [], "recent_merges degrades to empty list on gh failure, never raises")
    check(len(merge_warnings) == 1, "gh pr list failure surfaces exactly one warning")

    release = provider.get_release_state(None)
    check(release.match is None, "release.match is None (unknown) when no manifest/tag/release available")

    drift = provider.get_main_drift(Path(tempfile.mkdtemp()))
    check(drift.in_sync is None, "main_drift.in_sync is None when git fetch fails")
    check("git fetch" in drift.notes, "main_drift.notes explains the fetch failure")

    digest = build_digest(cfg, Path(tempfile.mkdtemp()), open_limit=50, merges_limit=10, provider=provider)
    d = digest.to_dict()
    validate_digest_schema(d, "zero-config fallback (fully degraded, still schema-valid)")
    check(len(d["warnings"]) >= 2, "degraded digest surfaces multiple warnings, never crashes")


def test_toml_get_zero_dependency_extraction():
    text = (
        '[github]\n'
        'gh_org = "YourOrg"\n'
        'gh_repo = "YourOrg/your-repo"\n'
        'project_number = 2  # inline comment\n'
        '\n'
        '[board]\n'
        'provider = "github"\n'
    )
    check(toml_get(text, "gh_org") == "YourOrg", "toml_get extracts gh_org")
    check(toml_get(text, "gh_repo") == "YourOrg/your-repo", "toml_get extracts gh_repo")
    check(toml_get(text, "project_number") == "2", "toml_get strips inline comments")
    check(toml_get(text, "provider") == "github", "toml_get extracts [board].provider")
    check(toml_get(text, "nonexistent_key") is None, "toml_get returns None for a missing key")


def main() -> int:
    test_board_configured_happy_path()
    test_zero_config_fallback()
    test_toml_get_zero_dependency_extraction()

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll board_reconcile self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
