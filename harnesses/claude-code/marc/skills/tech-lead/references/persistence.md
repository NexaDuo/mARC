### 6. Capture process improvements where they live (not just in chat)
Persist a new convention where it belongs, not only in per-session memory.
**Gated by context:** editing the plugin's own source (this skill,
`agents/*.md`) or PRing its home repo is legitimate ONLY in the plugin's
source repo (a file at `harnesses/claude-code/marc/.claude-plugin/plugin.json` whose `name` is `marc`) —
dogfooding. Elsewhere it's a privacy violation and futile (installed plugin
files are a read-only cache, overwritten on update).

- **Plugin source repo:** orchestration/dispatch → this skill; a
  discipline-specific rule → that agent definition. You MAY edit + PR it.
- **Any other repo — HARD PROHIBITION:** you MUST NOT edit the plugin's
  skill/agent files, and MUST NOT open an autonomous upstream pull request.
  Instead: a durable lesson → `AGENTS.md`; a scoped convention →
  `.agents/team.toml` (or `.claude/team.toml` on repos that
  haven't migrated); transient → the `process-improvements-buffer`
  memory note. See
  [upstream-contribution.md](upstream-contribution.md) for
  proposing product-level improvements (issue #22).

**Buffer (cheap, every time), flush (batched)** rather than an edit+PR per
tweak: a dated bullet in the buffer note, rolled into the plugin (source repo
only) or the consumer repo's AGENTS.md/team.toml in one PR at ≥ ~3 pending
items or the oldest ≥ 3 days old — except flush immediately for a tweak
affecting behavior active right now. A flush sweeps its own declaring file
for pre-existing violations and pairs the rule with a CI gate.

### 7. Materialize durable specialist artifacts (PEF file-write policy)
For a `@sec`/`@research` deliverable worth persisting (brief, report, decision
record), **you** materialize it: copy the comment into a file in the repo's
team-artifacts workspace (attribute the specialist, link the issue), landed
**via a reviewed PR**, never a direct commit — read-only specialists never get
write access. Workspace is a per-repo binding (`team.toml`'s `workspace_dir` or
AGENTS.md; reject absolute/`..` paths, treat as unset). This plugin's own
binding is `docs/marc/` (**public** GitHub Pages — nothing sensitive there). No
workspace defined → leave it in the comment (offer to establish one).

---

