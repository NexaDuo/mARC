#!/usr/bin/env python3
"""Self-test for `board.py`'s normalized `--json` output contract
(origin: #103; module renamed from `board_reconcile.py` to `board.py`,
origin: #128 — this test file keeps its name to match its established
test-discovery pattern).

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

from board import (  # noqa: E402
    BoardError,
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


def _set_status_run(project_view=None, field_list=None, item_list=None, item_edit_raises=None):
    """Builds a `run` callable that routes `gh project {view,field-list,item-list,
    item-edit}` (all share the ("gh","project") prefix, so `make_fake_run`'s
    two-token keying can't disambiguate them) to per-subcommand fixtures."""
    calls: list[list] = []

    def run(cmd: list) -> str:
        calls.append(cmd)
        if cmd[:3] == ["gh", "project", "view"]:
            if isinstance(project_view, Exception):
                raise project_view
            return project_view
        if cmd[:3] == ["gh", "project", "field-list"]:
            if isinstance(field_list, Exception):
                raise field_list
            return field_list
        if cmd[:3] == ["gh", "project", "item-list"]:
            if isinstance(item_list, Exception):
                raise item_list
            return item_list
        if cmd[:3] == ["gh", "project", "item-edit"]:
            if item_edit_raises:
                raise item_edit_raises
            return ""
        raise RuntimeError(f"unhandled set_status fixture: {cmd}")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def status_field_fixture() -> str:
    return json.dumps({
        "fields": [
            {"id": "PVTF_field1", "name": "Title"},
            {
                "id": "PVTF_status",
                "name": "Status",
                "type": "ProjectV2SingleSelectField",
                "options": [
                    {"id": "opt_todo", "name": "Todo"},
                    {"id": "opt_inprog", "name": "In Progress"},
                    {"id": "opt_blocked", "name": "Blocked"},
                    {"id": "opt_done", "name": "Done"},
                ],
            },
        ]
    })


def test_set_status_happy_path():
    """A valid issue + a valid target status resolves the correct field-id/
    option-id and issues exactly one `item-edit` call."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _set_status_run(
        project_view=json.dumps({"id": "PVT_project2"}),
        field_list=status_field_fixture(),
        item_list=project_item_list_fixture(),
    )
    provider = GitHubProvider(cfg, run=run)

    result = provider.set_status(103, "Done")
    check(result.item_id == "PVTI_abc123", "set_status resolves the item linked to the target issue")
    check(result.previous_status == "In Progress", "set_status records the previous status")
    check(result.status == "Done", "set_status echoes the requested status")
    check(result.field_id == "PVTF_status", "set_status resolves the Status field id")
    check(result.option_id == "opt_done", "set_status resolves the correct option-id for a valid status")

    edit_calls = [c for c in run.calls if c[:3] == ["gh", "project", "item-edit"]]  # type: ignore[attr-defined]
    check(len(edit_calls) == 1, "set_status issues exactly one item-edit call")
    edit_cmd = edit_calls[0]
    check("--single-select-option-id" in edit_cmd and "opt_done" in edit_cmd,
          "item-edit call carries the resolved option-id")
    check("--field-id" in edit_cmd and "PVTF_status" in edit_cmd,
          "item-edit call carries the resolved field-id")


def test_set_status_unknown_status_errors():
    """An unknown status name is rejected (never sends a bad option-id) with
    an error listing the project's actual valid options."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _set_status_run(
        project_view=json.dumps({"id": "PVT_project2"}),
        field_list=status_field_fixture(),
        item_list=project_item_list_fixture(),
    )
    provider = GitHubProvider(cfg, run=run)

    raised = False
    try:
        provider.set_status(103, "Kinda Done")
    except BoardError as e:
        raised = True
        check("Kinda Done" in str(e), "unknown-status error names the rejected status")
        check("Todo" in str(e) and "Done" in str(e), "unknown-status error lists the valid options")
    check(raised, "set_status raises BoardError for an unknown status name")

    edit_calls = [c for c in run.calls if c[:3] == ["gh", "project", "item-edit"]]  # type: ignore[attr-defined]
    check(len(edit_calls) == 0, "unknown status never reaches item-edit (no bad option-id sent)")


def test_set_status_missing_board_config_fails_loudly():
    """No project_number/gh_org configured -> BoardError, never a silent no-op
    (carries the #103 safety into status mutation)."""
    cfg = RepoConfig(gh_org=None, gh_repo="YourOrg/your-repo", project_number=None, provider="github")
    provider = GitHubProvider(cfg, run=lambda cmd: (_ for _ in ()).throw(RuntimeError("should not be called")))

    raised = False
    try:
        provider.set_status(103, "Done")
    except BoardError as e:
        raised = True
        check("no board configured" in str(e), "missing-board error explains why status can't be set")
    check(raised, "set_status raises BoardError (fails loudly) when no board is configured")


def test_set_status_issue_not_on_board_fails_loudly():
    """The issue exists but isn't linked to any item on this project board ->
    BoardError, never a silent no-op."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _set_status_run(
        project_view=json.dumps({"id": "PVT_project2"}),
        field_list=status_field_fixture(),
        item_list=project_item_list_fixture(),
    )
    provider = GitHubProvider(cfg, run=run)

    raised = False
    try:
        provider.set_status(999, "Done")
    except BoardError as e:
        raised = True
        check("999" in str(e), "item-not-found error names the missing issue number")
    check(raised, "set_status raises BoardError when the issue isn't on the project board")


def test_set_status_missing_project_scope_fails_loudly():
    """`gh project view` failing (e.g. missing `project` scope) surfaces as a
    BoardError, never a silent no-op."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _set_status_run(project_view=RuntimeError("HTTP 403: Resource not accessible (missing scope)"))
    provider = GitHubProvider(cfg, run=run)

    raised = False
    try:
        provider.set_status(103, "Done")
    except BoardError as e:
        raised = True
        check("project" in str(e).lower(), "missing-scope error mentions the `project` scope")
    check(raised, "set_status raises BoardError when gh project view fails")


def _create_run(issue_create=None, item_add=None, project_view=None, field_list=None, item_list=None):
    """Builds a `run` callable covering the full `create_issue` call chain:
    `gh issue create` -> `gh project item-add` -> (reused) `set_status`'s
    `gh project {view,field-list,item-list,item-edit}`."""
    calls: list[list] = []

    def run(cmd: list) -> str:
        calls.append(cmd)
        if cmd[:3] == ["gh", "issue", "create"]:
            if isinstance(issue_create, Exception):
                raise issue_create
            return issue_create
        if cmd[:3] == ["gh", "project", "item-add"]:
            if isinstance(item_add, Exception):
                raise item_add
            return item_add
        if cmd[:3] == ["gh", "project", "view"]:
            if isinstance(project_view, Exception):
                raise project_view
            return project_view
        if cmd[:3] == ["gh", "project", "field-list"]:
            if isinstance(field_list, Exception):
                raise field_list
            return field_list
        if cmd[:3] == ["gh", "project", "item-list"]:
            if isinstance(item_list, Exception):
                raise item_list
            return item_list
        if cmd[:3] == ["gh", "project", "item-edit"]:
            return ""
        raise RuntimeError(f"unhandled create_issue fixture: {cmd}")

    run.calls = calls  # type: ignore[attr-defined]
    return run


def new_issue_item_list_fixture() -> str:
    return json.dumps({
        "items": [
            {
                "id": "PVTI_new1",
                "status": "Todo",
                "content": {"number": 200, "title": "new issue", "url": "https://x/200"},
            },
        ]
    })


def test_create_issue_happy_path_with_board_and_status():
    """A title (+ body/labels) creates the issue, adds it to the configured
    board, and sets its initial Status — all in one `create_issue` call."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _create_run(
        issue_create="https://github.com/YourOrg/your-repo/issues/200\n",
        item_add=json.dumps({"id": "PVTI_new1"}),
        project_view=json.dumps({"id": "PVT_project2"}),
        field_list=status_field_fixture(),
        item_list=new_issue_item_list_fixture(),
    )
    provider = GitHubProvider(cfg, run=run)

    result = provider.create_issue("new issue", "body text", ["bug", "eng"], "Todo")
    check(result.issue_number == 200, "create_issue parses the issue number from the created issue's URL")
    check(result.issue_url == "https://github.com/YourOrg/your-repo/issues/200",
          "create_issue returns the created issue's URL")
    check(result.board_added is True, "create_issue adds the new issue to the configured board")
    check(result.board_item_id == "PVTI_new1", "create_issue records the board item id from item-add")
    check(result.status == "Todo", "create_issue sets and echoes the requested initial status")
    check(result.warnings == [], "create_issue happy path carries no warnings")

    create_calls = [c for c in run.calls if c[:3] == ["gh", "issue", "create"]]  # type: ignore[attr-defined]
    check(len(create_calls) == 1, "create_issue issues exactly one gh issue create call")
    check("--label" in create_calls[0], "create_issue passes labels through to gh issue create")


def test_create_issue_no_board_configured_degrades_gracefully():
    """No project_number/gh_org configured: the issue is still created, but
    board_added is False with an explanatory warning — the issue is never
    lost."""
    cfg = RepoConfig(gh_org=None, gh_repo="YourOrg/your-repo", project_number=None, provider="github")
    run = _create_run(issue_create="https://github.com/YourOrg/your-repo/issues/201\n")
    provider = GitHubProvider(cfg, run=run)

    result = provider.create_issue("no-board issue", "", [], status=None)
    check(result.issue_number == 201, "create_issue still creates the issue with no board configured")
    check(result.board_added is False, "create_issue reports board_added False with no board configured")
    check(any("no board configured" in w for w in result.warnings),
          "create_issue warns that no board is configured")


def test_create_issue_item_add_failure_keeps_the_issue():
    """`gh project item-add` failing (e.g. missing `project` scope) never
    loses the created issue — it degrades to a warning, board_added False."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _create_run(
        issue_create="https://github.com/YourOrg/your-repo/issues/202\n",
        item_add=RuntimeError("HTTP 403: Resource not accessible (missing scope)"),
    )
    provider = GitHubProvider(cfg, run=run)

    result = provider.create_issue("scope-missing issue", "", [], status="Todo")
    check(result.issue_number == 202, "create_issue still creates the issue when item-add fails")
    check(result.board_added is False, "create_issue reports board_added False when item-add fails")
    check(result.status is None, "create_issue never sets status when the board add itself failed")
    check(any("item-add failed" in w for w in result.warnings),
          "create_issue warns that item-add failed")


def test_create_issue_status_failure_keeps_the_board_add():
    """Board add succeeds but `set_status` fails (e.g. unknown status name):
    board_added stays True, status stays unset, and the failure surfaces as a
    warning rather than raising."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _create_run(
        issue_create="https://github.com/YourOrg/your-repo/issues/203\n",
        item_add=json.dumps({"id": "PVTI_new2"}),
        project_view=json.dumps({"id": "PVT_project2"}),
        field_list=status_field_fixture(),
        item_list=json.dumps({"items": []}),  # issue #203 not found -> set_status raises
    )
    provider = GitHubProvider(cfg, run=run)

    result = provider.create_issue("status-fail issue", "", [], status="Todo")
    check(result.board_added is True, "create_issue keeps board_added True when only set_status fails")
    check(result.status is None, "create_issue leaves status unset when set_status fails")
    check(any("setting status" in w for w in result.warnings),
          "create_issue warns that setting the initial status failed")


def test_create_issue_creation_failure_raises_loudly():
    """`gh issue create` itself failing MUST raise BoardError — this is the
    one step create_issue never degrades past, unlike the board-add/status
    steps that follow it."""
    cfg = RepoConfig(gh_org="YourOrg", gh_repo="YourOrg/your-repo", project_number=2, provider="github")
    run = _create_run(issue_create=RuntimeError("HTTP 422: Validation Failed"))
    provider = GitHubProvider(cfg, run=run)

    raised = False
    try:
        provider.create_issue("doomed issue", "", [], status=None)
    except BoardError as e:
        raised = True
        check("gh issue create failed" in str(e), "issue-create error names the failing gh call")
    check(raised, "create_issue raises BoardError when gh issue create itself fails")


def main() -> int:
    test_board_configured_happy_path()
    test_zero_config_fallback()
    test_toml_get_zero_dependency_extraction()
    test_set_status_happy_path()
    test_set_status_unknown_status_errors()
    test_set_status_missing_board_config_fails_loudly()
    test_set_status_issue_not_on_board_fails_loudly()
    test_set_status_missing_project_scope_fails_loudly()
    test_create_issue_happy_path_with_board_and_status()
    test_create_issue_no_board_configured_degrades_gracefully()
    test_create_issue_item_add_failure_keeps_the_issue()
    test_create_issue_status_failure_keeps_the_board_add()
    test_create_issue_creation_failure_raises_loudly()

    if _failures:
        print(f"\n{len(_failures)} FAILURE(S):")
        for m in _failures:
            print(f"  - {m}")
        return 1
    print("\nAll board_reconcile self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
