# Decision record: per-repo team binding moves from `team.config` to TOML (`team.toml`)

- **Type:** decision record
- **Date:** 2026-07-06
- **Attribution:** operator + user decision on
  [issue #51](https://github.com/NexaDuo/mARC/issues/51) (see the decision
  comment there); materialized by the operator per the `docs/marc/` landing
  process.
- **Status:** accepted (implemented in the PR that lands this record; plugin
  v0.11.0)

## Context

The per-repo team binding lived in `.claude/team.config`, a flat `key=value`
file with a custom extension. The reported pain: `team.config` maps to no
language in VS Code or GitHub, so keys/values/comments render colorless; the
naïve `key=value` shell parsing also forbade inline comments and forced
comma-separated strings where lists were meant (`app_paths`, and #46 had just
added `workspace_dir`).

## Options considered

- **INI rename** — zero-migration colorization, but keeps stringly-typed values.
- **TOML** — chosen.
- **YAML** — rejected: significant indentation is hostile to shell-edited
  config.
- **Editor-association only** — rejected: fixes VS Code but not GitHub
  rendering, and only for repo-settings users.

## Decision

**TOML**, at `.claude/team.toml`, accepting the breaking change while the
project is early. Rationale:

- Native syntax highlighting in VS Code and on GitHub.
- Real typed values and native lists (`app_paths = ["src/", "services/"]`)
  instead of comma-separated strings.
- Inline comments become legal (the old format forbade them because of the
  sed-based parsing).
- The SessionStart hook prints the file raw into model context, so any format
  "reads" — only the shell parse snippets need updating, and they must stay
  zero-dependency (no `yq`/TOML CLI assumed on consumer machines).

## Constraints carried into implementation

- **Shell-extraction discipline:** every key name stays unique across the whole
  file, so a key-anchored `sed` (tolerant of optional quotes and inline
  comments) extracts values without a TOML parser. CI enforces uniqueness and
  checks the sed extraction against `tomllib` on the canonical example.
- **Legacy handling is loud, not silent:** if only `.claude/team.config`
  exists, the SessionStart hook and `@techlead` print a one-line deprecation
  notice pointing at `/marc:init` migration; the old file is no longer parsed
  by any component.
- **`workspace_dir` containment rule (PR #50) carries over verbatim:** the
  value must be a relative, in-repo path — absolute paths and `..` components
  are rejected and treated as unset.
- Rider from #51: the remaining bare team handles in the example file were
  backticked while touching it (all GitHub-bound text escapes `@dev`-style
  handles).

## Consequences

- Consumer repos re-run `/marc:init` (or convert by hand from
  `docs/team.toml.example`) to migrate; the init skill carries the values over
  and offers to delete the obsolete file.
- `docs/team.config.example` is replaced by `docs/team.toml.example`.
- Breaking change shipped as plugin v0.11.0.
