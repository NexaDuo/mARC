---
name: init
handle: "@techlead"
description: >-
  Opt-in onboarding for the mARC agent team. Scaffolds a per-repo team binding so
  the team graduates from ephemeral session-memory to persistent, versioned
  config — without ever writing a file silently. Discovers the repo's org/repo/
  project at runtime via `gh`, prefills `{{ config_dir }}/team.toml`, and (optionally)
  a lean `AGENTS.md` skeleton and the `enabledPlugins` pin in
  `{{ config_dir }}/settings.json`. Each artifact is independently opt-in and is shown to
  you before anything is written. Invoke with /marc:init.
---

# /marc:init — opt-in onboarding & config scaffolding

You are running the **mARC onboarding flow**. Your job is to help the user turn a
zero-config repo into one with a **persistent, versioned team binding**, so
`@techlead` and the specialists stop relying on ephemeral session memory and read
the repo's concrete facts from `{{ config_dir }}/team.toml` (and optionally `AGENTS.md`)
at the start of every session.

## The one rule that overrides everything
**Nothing is ever written silently.** For every artifact you produce you MUST:
1. Discover / compose the exact content.
2. **Show that exact content to the user** (fenced, verbatim).
3. Ask for an **explicit "yes"** for *that specific artifact*.
4. Write **only** on that confirmation, to the path shown.

There are **three independent, individually opt-in artifacts**. The user may
accept any subset (including none). Never bundle them into one confirmation.
Never infer approval from silence or from a "yes" to a different artifact. If the
user declines everything, the repo is left **byte-for-byte unchanged** and
zero-config behavior is fully preserved.

Do not invent facts. Anything you cannot discover empirically becomes a
**clearly-marked `TODO` placeholder**, never a guess.

---

## Step 0 — Discover the repo facts at runtime (no hardcoded values)

Mirror the tech-lead skill's runtime-discovery pattern. Resolve values from `gh`
against the *checked-out* repo; do not hardcode any org, repo slug, or project
number in this flow.

**Batch the probes into ONE block.** Run all discovery in a single Bash
invocation so onboarding doesn't fire a permission prompt per `gh` call (a dogfood
run fired ~7). This block is read-only discovery — the **Write confirmations
below (steps 2-4 of the one rule) are the intentional safety gate and are NOT
what we are reducing**; every file write still stops for an explicit "yes".

```bash
# Where onboarding writes (the CONSUMING repo, not the plugin):
ROOT="${{{ project_dir_env }}:-$PWD}"

# --- ORG + REPO (from the checked-out repo) ---
GH_REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null)"
GH_ORG="${GH_REPO%%/*}"

# --- PROJECT (v2) candidates for the org, if any ---
# Needs the `project` scope; if it errors with "missing required scopes", tell the
# user to run `gh auth refresh -s project,read:project` once, then retry. Do not
# fabricate a number — leave it as a TODO if it can't be read. LIST candidates
# (number + title); the guard below decides — never auto-pick .projects[0].
echo "== org/repo: ${GH_REPO:-<unresolved>} =="
gh project list --owner "$GH_ORG" --format json 2>/dev/null \
  | jq -r '.projects[] | "\(.number)\t\(.title)"'
```

- If `gh` is not authenticated or a value cannot be read, keep going: use a
  `TODO` placeholder for the missing field and tell the user what to fill in.
- **Never silently bind to a default/"untitled" project.** `gh project list` often
  returns the owner's auto-created **"@owner's untitled project"** as number `1`;
  binding to it routes issues to the wrong board (a real dogfood bug). Decide:
  - **Exactly one match with a clear, non-empty title** → prefill
    `project_number` with it, but **tell the user which board** (number + title)
    you chose so they can confirm.
  - **The only match is generic/untitled** (title empty or literally
    `@owner's untitled project`) **OR there is more than one** match → do **not**
    guess. Either **ask the user which project** (AskUserQuestion), or leave
    `project_number` as an explicit `TODO_project_number` and write a one-line
    **warning comment** above it in the generated config. Never fix a silent guess.

The canonical schema you prefill from is `docs/team.toml.example` in the mARC
repo — match its sections, keys, and comments. Use generic placeholders (e.g.
`YourOrg/your-repo`) for anything you must illustrate but cannot discover.

---

## Artifact 1 — `{{ config_dir }}/team.toml`  (the core binding)

Prefill **only** the fields you discovered. Leave every other field as a clearly
labelled `TODO` — source paths, validation command, and release facts are
repo-specific and are **not** reliably discoverable, so never invent them.

Compose the content like this (substituting the discovered values; anything
unknown stays a `TODO`):

**Key hygiene (hard rule):** keep every KEY NAME UNIQUE across the whole file,
regardless of section — the plugin's shell snippets extract values with zero
dependencies (no TOML CLI) by key name alone, and a reused name would silently
resolve to the wrong value. Inline comments after a value are fine (TOML allows
them), but never put a `#` or `"` inside a quoted value.

```bash
# TOML is typed: a discovered project number is a bare integer; an unknown one
# must be a QUOTED placeholder string or the file won't parse. (Heredoc quote
# removal would strip quotes written inline in ${PROJ:-...}, hence this var.)
PROJ_TOML="${PROJ:-\"TODO_project_number\"}"

cat <<EOF
# mARC — per-repo team binding (generated by /marc:init; edit freely).
# Schema: docs/team.toml.example. Standard TOML; inline comments are fine.
# HARD RULE: keep every key name unique across the whole file — the plugin's
# zero-dependency shell snippets extract values by key name alone.

[github]
gh_org = "${GH_ORG:-TODO_your_org}"
gh_repo = "${GH_REPO:-TODO_owner/repo}"
# The GitHub Project (v2) number that is the team's source-of-truth board.
# WARNING if left as TODO: set this before dispatching, or issues may land on the
# wrong board. Do NOT use the owner's auto-created "untitled" project (often #1).
project_number = ${PROJ_TOML}
# Optional: uncomment to disambiguate when the org has several projects.
# project_title = "TODO_optional_title"

[architecture]
agents_doc = "AGENTS.md"

# Optional durable team-artifacts workspace (see docs/team.toml.example for the
# containment rule: relative in-repo path only, and mind publicly served dirs).
# [workspace]
# workspace_dir = "TODO"

[paths]
# TODO: set to this repo's real layout, e.g. app_paths = ["src/", "services/"]
app_paths = ["TODO"]
iac_paths = ["TODO"]
test_paths = ["TODO"]
ui_paths = ["TODO"]

[validation]
# The single command that proves a change works:
validation_command = "TODO"
# Optional smoke/health entrypoint:
# health_check_command = "TODO"

[release]
# true/false; if false, mandatory release phases are N/A.
has_release_pipeline = "TODO"
# Optional: public URLs to validate against.
# real_urls = ["TODO"]
# Optional: deploy model / non-negotiables specific to this repo.
# release_notes = "TODO"
EOF
```

Show the rendered content, then on an explicit "yes":

```bash
mkdir -p "$ROOT/{{ config_dir }}"
# ... write the shown content to "$ROOT/{{ config_dir }}/team.toml" ...
```

Never overwrite an existing `team.toml` without showing the user the current
file and the proposed one and getting explicit confirmation to replace it.

**Legacy migration (`team.config` → `team.toml`):** if the repo still has a
pre-0.11.0 `{{ config_dir }}/team.config`, carry its values into the TOML you compose
(same key names; comma-separated path strings become native TOML arrays), show
the result as usual, and on the user's "yes" write `team.toml` **and offer to
delete the obsolete `team.config`** (it is no longer parsed by any mARC
component — leaving it only re-triggers the deprecation notice).

---

## Artifact 2 — `AGENTS.md`  (optional; lean skeleton only)

Offer a **skeleton of section headings only** — no placebo prose. Anti-anchoring
lesson: capture only what is **not** discoverable by convention; do not pre-write
architecture claims the model would otherwise infer from the tree. The headings
are prompts for the user to fill; the `<!-- -->` lines are guidance, not facts.

Show exactly this (adjust nothing but let the user edit after):

```markdown
# AGENTS.md

<!-- The authority @techlead and the specialists respect. Keep it lean: record
     only what is NOT obvious from reading the repo. Delete these comments. -->

## Architecture
<!-- The few load-bearing facts a newcomer can't infer from the tree. -->

## Constraints
<!-- Non-negotiables: reproducibility/no-manual-drift, protected data stores,
     tooling AVOID lists, config model, etc. -->

## Release phases
<!-- What "done" requires here: staging -> smoke -> prod -> smoke, real URLs,
     CI to green. If there is no pipeline yet, say so explicitly. -->

## Lessons
<!-- Durable, hard-won lessons and past incidents worth not repeating. -->
```

On an explicit "yes", write it to `$ROOT/AGENTS.md`. If `AGENTS.md` already
exists, **do not touch it** — show the user it exists and stop for that artifact.

---

## Artifact 3 — `enabledPlugins` pin in `{{ config_dir }}/settings.json`  (adopt for good)

This is the **heaviest commitment** — it pins mARC on for this repo for everyone
who works in it. Frame it deliberately as the *"adopt mARC for good"* step, not a
casual default. Offer it last and only if the user wants durable enablement.

**Merge, never clobber.** Discover the plugin's real identifier at runtime (it is
`<plugin>@<marketplace>`; do not hardcode it), then merge the pin into any
existing settings, preserving all other keys.

```bash
SETTINGS="$ROOT/{{ config_dir }}/settings.json"

# Discover the installed plugin id (e.g. marc@<marketplace>) at runtime.
PLUGIN_ID="$({{ plugin_list_command }} --json 2>/dev/null \
  | jq -r '.[] | select(.name=="marc") | .id' | head -n1)"
# Fall back to asking the user for the id if it can't be read; never invent it.

# Compose the merged result WITHOUT writing yet, so it can be shown first.
# Start from existing settings if present, else an empty object; deep-merge the
# enabledPlugins entry so no sibling key is lost.
BASE='{}'; [ -f "$SETTINGS" ] && BASE="$(cat "$SETTINGS")"
printf '%s' "$BASE" | jq --arg id "$PLUGIN_ID" \
  '.enabledPlugins = ((.enabledPlugins // {}) + {($id): true})'
```

Show the merged JSON (the full resulting file, so the user sees nothing else
changed), and on an explicit "yes" write that exact JSON to
`$ROOT/{{ config_dir }}/settings.json`. Verify it still parses (`jq . "$SETTINGS"`) after
writing. If `PLUGIN_ID` is empty, do not write — ask the user for the correct
`<plugin>@<marketplace>` id first.

---

## Wrap up
Summarize which of the three artifacts were written (with their absolute paths)
and which were skipped. If `team.toml` was written, remind the user that the
SessionStart hook will print it into context next session, and that any `TODO`
fields should be filled in for the specialists to rely on them. Suggest
`/marc:init` can be re-run any time to add the artifacts they skipped.
