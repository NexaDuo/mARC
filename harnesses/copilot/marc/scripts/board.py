#!/usr/bin/env python3
"""Board operator script (origin: #103, extended #105, #113).

ONE-call replacement for the `@techlead` skill's hand-rolled `gh` board
sequences. Three subcommands:

  * `reconcile`   — replaces the `gh issue list` / `gh pr list` /
                    `gh release view` / `git fetch` reconciliation snippets.
                    Reads every repo fact from `team.toml` at runtime
                    (org/repo/board number, using the skill's own
                    zero-dependency extraction fallbacks — no hardcoded
                    slugs) and prints a normalized, PROVIDER-AGNOSTIC digest
                    of board/PR/release/drift state:
                      - open board items — id/title/status/assignee/linked_pr
                      - recent merges
                      - release state (does the plugin manifest version match
                        the latest tag/release?)
                      - local <-> remote `main` drift
  * `set-status`  — replaces the inline `gh project view` / `field-list` /
                    `item-list` / `item-edit` sequence the skill embedded for
                    moving a board item's Status. One call: issue number +
                    target status name in, the item's Status field mutated.
  * `create`      — replaces the `gh issue create` / `gh project item-add` /
                    set-status sequence the skill hand-rolled to file a new
                    tracked issue. One call: title (+ body/labels) in, an
                    issue created, best-effort added to the configured board,
                    and (if `--status` given) its initial Status set — reusing
                    the same `team.toml` resolution and `set_status` code as
                    above. Degrades gracefully: a missing `project` scope or
                    unconfigured board never loses the created issue, it only
                    surfaces a `warnings` entry with `board_added=False`.

Usage:
    python3 board.py reconcile [--json] [--team-toml PATH]
                                [--repo-root PATH]
                                [--merges-limit N] [--open-limit N]
    python3 board.py set-status --issue N --status NAME
                                [--team-toml PATH] [--repo-root PATH]
                                [--json]
    python3 board.py create --title TITLE
                                [--body TEXT | --body-file PATH]
                                [--labels a,b,c] [--status NAME]
                                [--team-toml PATH] [--repo-root PATH]
                                [--json]

    (no subcommand defaults to `reconcile` for backward compatibility with
    existing callers.)

    Renamed from `board_reconcile.py` (origin: #128). A `board_reconcile.py`
    shim in this same directory delegates to this module for one release so
    existing callers (including bare `board_reconcile.py --json`) keep
    working unchanged.

    --json           machine-readable output on stdout. Default is a short
                      human-readable summary.
    --team-toml PATH override the team.toml path (default:
                      <repo-root>/.claude/team.toml).
    --repo-root PATH  override the repo root used for git/manifest lookups
                      (default: current working directory).
    --issue N         (set-status only) the issue/PR number the board item is
                      linked to.
    --status NAME     (set-status only) target status name, validated against
                      the project's actual Status field options (e.g. Todo,
                      "In Progress", Blocked, Done — exact match required).

`reconcile` degrades gracefully: a missing `project` scope, a missing/
ambiguous board, no `gh` auth, or no releases/tags never crashes the script —
each gap surfaces as a `warnings` entry and the corresponding digest section
reports what it can (never guesses, never auto-picks an untitled board — same
rule the skill enforces at dispatch time).

`set-status` is the opposite by design: it never degrades quietly. If the
board can't be resolved (missing `project_number`/`gh_org`, missing `project`
scope, project/field/item lookup failure) or the requested status isn't one of
the project's actual Status options, it FAILS LOUDLY (raises `BoardError`,
exits non-zero with a clear message) rather than silently no-op'ing a status
change.

Provider architecture: `BoardProvider` is the abstract contract; `GitHubProvider`
is the only concrete implementation today. A future Azure DevOps / Jira
provider plugs in by implementing the same methods (including `set_status`)
and emitting the same normalized shapes — nothing downstream (this CLI, the
skill, tests) needs to change.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Optional

SCHEMA_VERSION = 1


class BoardError(Exception):
    """Raised by `set_status` (and friends) when the board/status can't be
    resolved unambiguously. Callers MUST surface this loudly (never catch and
    silently no-op) — carries the #103 safety into status mutation (#105)."""

# --- team.toml zero-dependency extraction (mirrors the skill's `toml_get`) --
# Same discipline as the shell `sed` pattern the skill uses: key names are
# unique across the whole file (CI enforces this, see
# ".github/workflows/ci.yml" team.toml schema contract), so a single
# key-anchored regex is safe without a TOML parser dependency on the consumer
# machine. Call with LITERAL key names only.
_TOML_KEY_RE_TEMPLATE = r'^[ \t]*{key}[ \t]*=[ \t]*"?([^"#\n]*)"?'


def toml_get(text: str, key: str) -> Optional[str]:
    pattern = re.compile(_TOML_KEY_RE_TEMPLATE.format(key=re.escape(key)), re.MULTILINE)
    m = pattern.search(text)
    if not m:
        return None
    val = m.group(1).strip()
    return val or None


@dataclass
class RepoConfig:
    gh_org: Optional[str] = None
    gh_repo: Optional[str] = None
    project_number: Optional[int] = None
    project_title: Optional[str] = None
    provider: str = "github"
    plugin_manifest_path: Optional[str] = None

    @classmethod
    def from_team_toml(cls, path: Path) -> "RepoConfig":
        if not path.is_file():
            return cls()
        text = path.read_text(encoding="utf-8")
        proj_raw = toml_get(text, "project_number")
        project_number = int(proj_raw) if proj_raw and proj_raw.isdigit() else None
        return cls(
            gh_org=toml_get(text, "gh_org"),
            gh_repo=toml_get(text, "gh_repo"),
            project_number=project_number,
            project_title=toml_get(text, "project_title"),
            provider=toml_get(text, "provider") or "github",
            plugin_manifest_path=toml_get(text, "agents_doc"),
        )


# --- normalized, provider-agnostic digest shapes ----------------------------

@dataclass
class BoardItem:
    id: str
    title: str
    status: str
    assignee: Optional[str]
    linked_pr: Optional[int]


@dataclass
class RecentMerge:
    number: int
    title: str
    merged_at: Optional[str]
    url: str


@dataclass
class ReleaseState:
    manifest_version: Optional[str]
    latest_tag: Optional[str]
    latest_release_tag: Optional[str]
    match: Optional[bool]
    notes: str


@dataclass
class StatusResult:
    item_id: str
    issue_number: int
    previous_status: Optional[str]
    status: str
    field_id: str
    option_id: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CreateResult:
    issue_number: int
    issue_url: str
    board_added: bool
    board_item_id: Optional[str]
    status: Optional[str]
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MainDrift:
    local_sha: Optional[str]
    remote_sha: Optional[str]
    ahead: Optional[int]
    behind: Optional[int]
    in_sync: Optional[bool]
    notes: str


@dataclass
class NormalizedDigest:
    schema_version: int
    provider: str
    repo: Optional[str]
    board_configured: bool
    board_number: Optional[int]
    board_title: Optional[str]
    items: list = field(default_factory=list)
    recent_merges: list = field(default_factory=list)
    release: Optional[dict] = None
    main_drift: Optional[dict] = None
    warnings: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# --- provider interface -----------------------------------------------------

class BoardProvider(ABC):
    """Provider-agnostic contract. A future Azure/Jira provider implements
    these four methods and returns the same normalized shapes; nothing else
    in the digest pipeline changes."""

    @abstractmethod
    def list_open_items(self, limit: int) -> tuple[list[BoardItem], bool, Optional[int], Optional[str], list[str]]:
        """Returns (items, board_configured, board_number, board_title, warnings)."""

    @abstractmethod
    def list_recent_merges(self, limit: int) -> tuple[list[RecentMerge], list[str]]:
        ...

    @abstractmethod
    def get_release_state(self, manifest_path: Optional[Path]) -> ReleaseState:
        ...

    @abstractmethod
    def get_main_drift(self, repo_root: Path) -> MainDrift:
        ...

    @abstractmethod
    def set_status(self, issue_number: int, status: str) -> StatusResult:
        """Resolve the board item linked to `issue_number` and move it to
        `status` (must exactly match one of the project's Status field
        options). MUST raise `BoardError` — never silently no-op — if the
        board can't be resolved, the item isn't found, or `status` isn't a
        valid option."""

    @abstractmethod
    def create_issue(self, title: str, body: str, labels: list[str],
                      status: Optional[str]) -> CreateResult:
        """Create an issue, best-effort add it to the configured board, and
        best-effort set its initial `status` (reusing `set_status`). MUST
        raise `BoardError` only if issue CREATION itself fails — a missing
        `project` scope, an unconfigured board, or a `set_status` failure
        must degrade to a `warnings` entry with `board_added=False`, never
        lose the created issue."""


Runner = Callable[[list], str]


def _default_runner(cmd: list) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or f"command failed: {' '.join(cmd)}")
    return result.stdout


class GitHubProvider(BoardProvider):
    """Concrete provider backed by the `gh` CLI + local `git`. Every external
    call is routed through `self._run` so tests can inject fixture output
    without a network call or `gh` install."""

    def __init__(self, config: RepoConfig, run: Runner = _default_runner):
        self.config = config
        self._run = run

    def _repo_flag(self) -> list:
        return ["--repo", self.config.gh_repo] if self.config.gh_repo else []

    def list_open_items(self, limit: int = 50):
        warnings: list[str] = []
        items: list[BoardItem] = []
        board_configured = bool(self.config.project_number and self.config.gh_org)

        if not board_configured:
            warnings.append(
                "no board configured (missing project_number/gh_org in team.toml) "
                "— falling back to open issues, Status is the raw issue state"
            )
            try:
                raw = self._run(
                    ["gh", "issue", "list", "--state", "open", "--limit", str(limit),
                     "--json", "number,title,assignees"] + self._repo_flag()
                )
                for it in json.loads(raw):
                    assignees = it.get("assignees") or []
                    items.append(BoardItem(
                        id=f"issue-{it['number']}",
                        title=it["title"],
                        status="open",
                        assignee=assignees[0]["login"] if assignees else None,
                        linked_pr=None,
                    ))
            except Exception as e:  # noqa: BLE001 - degrade, never crash
                warnings.append(f"gh issue list failed: {e}")
            return items, board_configured, self.config.project_number, self.config.project_title, warnings

        try:
            raw = self._run(
                ["gh", "project", "item-list", str(self.config.project_number),
                 "--owner", self.config.gh_org, "--format", "json", "--limit", str(limit)]
            )
            payload = json.loads(raw)
        except Exception as e:  # noqa: BLE001
            warnings.append(f"gh project item-list failed (missing `project` scope? run "
                             f"`gh auth refresh -s project,read:project`): {e}")
            return items, board_configured, self.config.project_number, self.config.project_title, warnings

        for it in payload.get("items", []):
            content = it.get("content", {})
            number = content.get("number")
            linked_pr = self._linked_pr(number) if number else None
            items.append(BoardItem(
                id=it.get("id", f"item-{number}"),
                title=content.get("title", ""),
                status=it.get("status", "Unknown"),
                assignee=None,  # gh project item-list does not expose assignees.
                linked_pr=linked_pr,
            ))
        return items, board_configured, self.config.project_number, self.config.project_title, warnings

    def _linked_pr(self, issue_number: int) -> Optional[int]:
        if not self.config.gh_repo or "/" not in self.config.gh_repo:
            return None
        owner, repo = self.config.gh_repo.split("/", 1)
        query = (
            "query($owner:String!,$repo:String!,$n:Int!) { repository(owner:$owner, name:$repo) "
            "{ issue(number:$n) { closedByPullRequestsReferences(first:1) { nodes { number } } } } }"
        )
        try:
            raw = self._run([
                "gh", "api", "graphql",
                "-f", f"query={query}",
                "-f", f"owner={owner}",
                "-f", f"repo={repo}",
                "-F", f"n={issue_number}",
            ])
            data = json.loads(raw)
            nodes = (data.get("data", {}).get("repository", {}).get("issue") or {}) \
                .get("closedByPullRequestsReferences", {}).get("nodes", [])
            return nodes[0]["number"] if nodes else None
        except Exception:  # noqa: BLE001 - linked-PR lookup is best-effort
            return None

    def list_recent_merges(self, limit: int = 10):
        warnings: list[str] = []
        merges: list[RecentMerge] = []
        try:
            raw = self._run(
                ["gh", "pr", "list", "--state", "merged", "--limit", str(limit),
                 "--json", "number,title,mergedAt,url"] + self._repo_flag()
            )
            for pr in json.loads(raw):
                merges.append(RecentMerge(
                    number=pr["number"], title=pr["title"],
                    merged_at=pr.get("mergedAt"), url=pr["url"],
                ))
        except Exception as e:  # noqa: BLE001
            warnings.append(f"gh pr list --state merged failed: {e}")
        return merges, warnings

    def get_release_state(self, manifest_path: Optional[Path]) -> ReleaseState:
        manifest_version = None
        if manifest_path and manifest_path.is_file():
            try:
                manifest_version = json.loads(manifest_path.read_text(encoding="utf-8")).get("version")
            except Exception:  # noqa: BLE001
                pass

        latest_tag = None
        try:
            raw = self._run(["git", "describe", "--tags", "--abbrev=0"])
            latest_tag = raw.strip() or None
        except Exception:  # noqa: BLE001
            pass

        latest_release_tag = None
        try:
            raw = self._run(
                ["gh", "release", "view", "--json", "tagName"] + self._repo_flag()
            )
            latest_release_tag = json.loads(raw).get("tagName")
        except Exception:  # noqa: BLE001
            pass

        reference_tag = latest_release_tag or latest_tag
        match = None
        notes = ""
        if manifest_version is None:
            notes = "no manifest version found (plugin.json missing or unreadable)"
        elif reference_tag is None:
            notes = "no git tag / GitHub release found to compare against"
        else:
            normalized_tag = reference_tag.lstrip("v")
            match = normalized_tag == manifest_version
            notes = "manifest version matches latest tag/release" if match \
                else f"manifest version {manifest_version} != latest tag/release {reference_tag}"

        return ReleaseState(
            manifest_version=manifest_version,
            latest_tag=latest_tag,
            latest_release_tag=latest_release_tag,
            match=match,
            notes=notes,
        )

    def get_main_drift(self, repo_root: Path) -> MainDrift:
        try:
            self._run(["git", "-C", str(repo_root), "fetch", "origin", "main", "--quiet"])
        except Exception as e:  # noqa: BLE001
            return MainDrift(None, None, None, None, None, f"git fetch origin main failed: {e}")

        try:
            local_sha = self._run(["git", "-C", str(repo_root), "rev-parse", "main"]).strip()
        except Exception:  # noqa: BLE001
            local_sha = None
        try:
            remote_sha = self._run(["git", "-C", str(repo_root), "rev-parse", "origin/main"]).strip()
        except Exception:  # noqa: BLE001
            remote_sha = None

        ahead = behind = None
        in_sync = None
        notes = ""
        if local_sha and remote_sha:
            if local_sha == remote_sha:
                ahead = behind = 0
                in_sync = True
                notes = "local main == origin/main"
            else:
                try:
                    counts = self._run(
                        ["git", "-C", str(repo_root), "rev-list", "--left-right", "--count",
                         "origin/main...main"]
                    ).strip().split()
                    behind, ahead = int(counts[0]), int(counts[1])
                    in_sync = ahead == 0 and behind == 0
                    notes = f"local main is {ahead} ahead / {behind} behind origin/main"
                except Exception as e:  # noqa: BLE001
                    notes = f"could not compute ahead/behind: {e}"
        else:
            notes = "no local `main` branch checked out — cannot compute drift"

        return MainDrift(local_sha, remote_sha, ahead, behind, in_sync, notes)

    def set_status(self, issue_number: int, status: str) -> StatusResult:
        if not (self.config.project_number and self.config.gh_org):
            raise BoardError(
                "no board configured (missing project_number/gh_org in team.toml) "
                "— cannot set status without a resolvable project; refusing to "
                "guess or auto-bind an untitled/ambiguous board"
            )

        try:
            project_raw = self._run(
                ["gh", "project", "view", str(self.config.project_number),
                 "--owner", self.config.gh_org, "--format", "json"]
            )
            project = json.loads(project_raw)
            project_id = project.get("id")
        except Exception as e:  # noqa: BLE001
            raise BoardError(
                f"gh project view failed (missing `project` scope? run "
                f"`gh auth refresh -s project,read:project`): {e}"
            ) from e
        if not project_id:
            raise BoardError(
                f"gh project view returned no project id for #{self.config.project_number} "
                f"(owner {self.config.gh_org}) — cannot resolve board unambiguously"
            )

        try:
            fields_raw = self._run(
                ["gh", "project", "field-list", str(self.config.project_number),
                 "--owner", self.config.gh_org, "--format", "json"]
            )
            fields = json.loads(fields_raw).get("fields", [])
        except Exception as e:  # noqa: BLE001
            raise BoardError(f"gh project field-list failed: {e}") from e

        status_field = next((f for f in fields if f.get("name") == "Status"), None)
        if not status_field:
            raise BoardError(
                f"project #{self.config.project_number} has no 'Status' field — "
                f"cannot set status"
            )
        options = status_field.get("options", [])
        option = next((o for o in options if o.get("name") == status), None)
        if not option:
            valid = ", ".join(o.get("name", "") for o in options) or "(none)"
            raise BoardError(
                f"unknown status '{status}' — valid options for this project's "
                f"Status field are: {valid}"
            )

        try:
            items_raw = self._run(
                ["gh", "project", "item-list", str(self.config.project_number),
                 "--owner", self.config.gh_org, "--format", "json", "--limit", "200"]
            )
            items = json.loads(items_raw).get("items", [])
        except Exception as e:  # noqa: BLE001
            raise BoardError(f"gh project item-list failed: {e}") from e

        item = next((it for it in items if (it.get("content") or {}).get("number") == issue_number), None)
        if not item:
            raise BoardError(
                f"issue #{issue_number} was not found on project #{self.config.project_number} "
                f"— has it been added to the board? (`gh project item-add`)"
            )

        previous_status = item.get("status")
        try:
            self._run([
                "gh", "project", "item-edit",
                "--id", item["id"],
                "--project-id", project_id,
                "--field-id", status_field["id"],
                "--single-select-option-id", option["id"],
            ])
        except Exception as e:  # noqa: BLE001
            raise BoardError(f"gh project item-edit failed: {e}") from e

        return StatusResult(
            item_id=item["id"],
            issue_number=issue_number,
            previous_status=previous_status,
            status=status,
            field_id=status_field["id"],
            option_id=option["id"],
        )

    def create_issue(self, title: str, body: str, labels: list[str],
                      status: Optional[str] = None) -> CreateResult:
        cmd = ["gh", "issue", "create", "--title", title, "--body", body] + self._repo_flag()
        for label in labels:
            cmd += ["--label", label]

        try:
            raw = self._run(cmd)
        except Exception as e:  # noqa: BLE001 - issue creation itself must fail loudly
            raise BoardError(f"gh issue create failed: {e}") from e

        lines = [ln.strip() for ln in raw.strip().splitlines() if ln.strip()]
        url = lines[-1] if lines else ""
        m = re.search(r"/issues/(\d+)", url)
        if not m:
            raise BoardError(f"gh issue create succeeded but returned no parseable issue URL: {raw!r}")
        issue_number = int(m.group(1))

        warnings: list[str] = []
        board_added = False
        board_item_id: Optional[str] = None
        result_status: Optional[str] = None

        if not (self.config.project_number and self.config.gh_org):
            warnings.append(
                "no board configured (missing project_number/gh_org in team.toml) "
                f"— issue #{issue_number} created but not added to any board"
            )
        else:
            try:
                add_raw = self._run([
                    "gh", "project", "item-add", str(self.config.project_number),
                    "--owner", self.config.gh_org, "--url", url, "--format", "json",
                ])
                item = json.loads(add_raw)
                board_item_id = item.get("id")
                board_added = True
            except Exception as e:  # noqa: BLE001 - board add is best-effort, never lose the issue
                warnings.append(
                    f"gh project item-add failed (missing `project` scope? run "
                    f"`gh auth refresh -s project,read:project`) — issue #{issue_number} was "
                    f"created but NOT added to the board: {e}"
                )

            if board_added and status:
                try:
                    status_result = self.set_status(issue_number, status)
                    result_status = status_result.status
                except BoardError as e:
                    warnings.append(
                        f"issue #{issue_number} was added to the board but setting status "
                        f"{status!r} failed: {e}"
                    )

        return CreateResult(
            issue_number=issue_number,
            issue_url=url,
            board_added=board_added,
            board_item_id=board_item_id,
            status=result_status,
            warnings=warnings,
        )


# --- digest assembly ---------------------------------------------------------

PROVIDERS = {"github": GitHubProvider}


def build_digest(config: RepoConfig, repo_root: Path, open_limit: int, merges_limit: int,
                  provider: Optional[BoardProvider] = None) -> NormalizedDigest:
    warnings: list[str] = []
    if provider is None:
        provider_cls = PROVIDERS.get(config.provider)
        if provider_cls is None:
            warnings.append(f"unknown board provider '{config.provider}', falling back to github")
            provider_cls = GitHubProvider
        provider = provider_cls(config)

    items, board_configured, board_number, board_title, w1 = provider.list_open_items(open_limit)
    merges, w2 = provider.list_recent_merges(merges_limit)

    manifest_path = None
    default_manifest = repo_root / "harnesses" / "claude-code" / "marc" / ".claude-plugin" / "plugin.json"
    root_manifest = repo_root / ".claude-plugin" / "plugin.json"
    if default_manifest.is_file():
        manifest_path = default_manifest
    elif root_manifest.is_file():
        manifest_path = root_manifest
    release = provider.get_release_state(manifest_path)
    drift = provider.get_main_drift(repo_root)

    return NormalizedDigest(
        schema_version=SCHEMA_VERSION,
        provider=config.provider,
        repo=config.gh_repo,
        board_configured=board_configured,
        board_number=board_number,
        board_title=board_title,
        items=[asdict(i) for i in items],
        recent_merges=[asdict(m) for m in merges],
        release=asdict(release),
        main_drift=asdict(drift),
        warnings=warnings + w1 + w2,
    )


def render_human(digest: NormalizedDigest) -> str:
    lines = [
        f"board ({digest.provider}) — {digest.repo or '(repo unknown)'}",
        f"board: {'#' + str(digest.board_number) + ' ' + (digest.board_title or '') if digest.board_configured else 'not configured'}",
        "",
        f"open items ({len(digest.items)}):",
    ]
    for it in digest.items[:20]:
        pr = f" -> PR #{it['linked_pr']}" if it.get("linked_pr") else ""
        assignee = f" [{it['assignee']}]" if it.get("assignee") else ""
        lines.append(f"  - [{it['status']}] {it['title']}{assignee}{pr}")
    lines.append("")
    lines.append(f"recent merges ({len(digest.recent_merges)}):")
    for m in digest.recent_merges[:10]:
        lines.append(f"  - #{m['number']} {m['title']} ({m.get('merged_at') or 'unknown date'})")
    lines.append("")
    rel = digest.release or {}
    lines.append(f"release: manifest={rel.get('manifest_version')} tag={rel.get('latest_tag')} "
                 f"release={rel.get('latest_release_tag')} match={rel.get('match')} — {rel.get('notes')}")
    drift = digest.main_drift or {}
    lines.append(f"main drift: ahead={drift.get('ahead')} behind={drift.get('behind')} "
                 f"in_sync={drift.get('in_sync')} — {drift.get('notes')}")
    if digest.warnings:
        lines.append("")
        lines.append("warnings:")
        for w in digest.warnings:
            lines.append(f"  ! {w}")
    return "\n".join(lines)


def _resolve_config(args: argparse.Namespace) -> RepoConfig:
    repo_root = Path(args.repo_root).resolve()
    team_toml = Path(args.team_toml) if args.team_toml else repo_root / ".claude" / "team.toml"
    config = RepoConfig.from_team_toml(team_toml)

    if not config.gh_repo:
        # Zero-config fallback, same as the skill: discover from the checked-out repo.
        try:
            config.gh_repo = _default_runner(
                ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]
            ).strip()
        except Exception:  # noqa: BLE001
            pass
    if not config.gh_org and config.gh_repo and "/" in config.gh_repo:
        config.gh_org = config.gh_repo.split("/", 1)[0]
    return config


def _make_provider(config: RepoConfig) -> BoardProvider:
    provider_cls = PROVIDERS.get(config.provider, GitHubProvider)
    return provider_cls(config)


def _cmd_reconcile(args: argparse.Namespace) -> int:
    repo_root = Path(args.repo_root).resolve()
    config = _resolve_config(args)
    digest = build_digest(config, repo_root, args.open_limit, args.merges_limit)

    if args.json:
        print(json.dumps(digest.to_dict(), indent=2, sort_keys=True))
    else:
        print(render_human(digest))
    return 0


def _cmd_set_status(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    provider = _make_provider(config)
    try:
        result = provider.set_status(args.issue, args.status)
    except BoardError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"issue #{result.issue_number}: {result.previous_status!r} -> {result.status!r} "
              f"(item {result.item_id})")
    return 0


def _cmd_create(args: argparse.Namespace) -> int:
    config = _resolve_config(args)
    provider = _make_provider(config)

    if args.body_file:
        body = Path(args.body_file).read_text(encoding="utf-8")
    elif args.body is not None:
        body = args.body
    else:
        body = ""
    labels = [l.strip() for l in args.labels.split(",") if l.strip()] if args.labels else []

    try:
        result = provider.create_issue(args.title, body, labels, args.status)
    except BoardError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    else:
        print(f"created issue #{result.issue_number}: {result.issue_url}")
        print(f"  board_added={result.board_added} board_item_id={result.board_item_id} "
              f"status={result.status!r}")
        for w in result.warnings:
            print(f"  ! {w}")
    return 0


def _add_common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--json", action="store_true", help="machine-readable output on stdout")
    p.add_argument("--team-toml", default=None, help="override team.toml path")
    p.add_argument("--repo-root", default=".", help="repo root for git/manifest lookups")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subparsers = parser.add_subparsers(dest="command")

    reconcile_p = subparsers.add_parser("reconcile", help="print the normalized board/PR/release/drift digest")
    _add_common_args(reconcile_p)
    reconcile_p.add_argument("--merges-limit", type=int, default=10)
    reconcile_p.add_argument("--open-limit", type=int, default=50)
    reconcile_p.set_defaults(func=_cmd_reconcile)

    set_status_p = subparsers.add_parser("set-status", help="move a board item's Status in one call")
    _add_common_args(set_status_p)
    set_status_p.add_argument("--issue", type=int, required=True, help="issue/PR number linked to the board item")
    set_status_p.add_argument("--status", required=True, help="target status name (e.g. Todo, 'In Progress', Blocked, Done)")
    set_status_p.set_defaults(func=_cmd_set_status)

    create_p = subparsers.add_parser(
        "create", help="create an issue, add it to the board, and set its initial Status — one call"
    )
    _add_common_args(create_p)
    create_p.add_argument("--title", required=True, help="issue title")
    create_p.add_argument("--body-file", default=None, help="path to a file containing the issue body")
    create_p.add_argument("--body", default=None, help="inline issue body (use --body-file for longer text)")
    create_p.add_argument("--labels", default=None, help="comma-separated label names")
    create_p.add_argument(
        "--status", default=None,
        help="initial Status to set after adding to the board (requires a configured board; "
             "skipped with a warning if the board add fails or no board is configured)",
    )
    create_p.set_defaults(func=_cmd_create)

    # Backward compatibility: no subcommand -> default to `reconcile` so
    # existing callers (`board.py --json`, or the `board_reconcile.py` shim)
    # keep working unchanged.
    argv = sys.argv[1:]
    if not argv or argv[0] not in ("reconcile", "set-status", "create", "-h", "--help"):
        argv = ["reconcile"] + argv

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
