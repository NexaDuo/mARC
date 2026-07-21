# Research brief: can `agy` (Google Antigravity CLI) install the mARC harness without a `git clone`, via a marketplace one-liner?

> **Artifact record** — type: `brief` · produced by: the `@research` specialist
> (mARC team) · date: 2026-07-21 · motivating issue:
> [NexaDuo/mARC#151](https://github.com/NexaDuo/mARC/issues/151) · original:
> [issue comment](https://github.com/NexaDuo/mARC/issues/151#issuecomment-5039999661)
> · outcome: no clone-free install path exists today; no shipping change
> (docs, manifests, and `README.md` install/update instructions are already
> accurate and are left as-is).

**TL;DR** — No. A `plugin@marketplace` install form is documented in `agy`'s
own help text, but the marketplace registry it resolves against is
closed/Google-internal, not user-registerable by pointing at a repo manifest.
`agy plugin install <target>` only accepts a **local directory path** in
practice; there is no discovered registration step for a custom marketplace,
and mARC's existing `.claude-plugin/marketplace.json` (Claude Code/Copilot
schema) is not read by `agy` at all. Recommendation: keep the clone-based
install docs for Antigravity as-is; no manifest change.

## Findings

1. **`agy plugin install <target>` requires a local directory; the
   `plugin@marketplace` form fails with `unknown marketplace: <name>` for any
   non-built-in name** — measured (observed from installed binary, `agy`
   v1.1.4, run in the mARC repo checkout).
   - `agy plugin install marc@nexaduo` → `Error: unknown marketplace: nexaduo`
   - `agy plugin install nexaduo/marc` → `Error: install target must be a
     directory: nexaduo/marc`
   - `agy plugin install ./harnesses/antigravity/marc` (local dir) → succeeds:
     `[ok] marc` / `✔ skills: 2 processed` / `✔ agents: 6 processed`.
   - Only the local-directory form works; that requires the repo to already
     exist on disk (cloned or otherwise fetched).

2. **`agy plugin link <mp> <target>` ("Generate link to a marketplace") also
   fails with the same `unknown marketplace: <name>` error regardless of
   target**, including `agy plugin link nexaduo .` and
   `agy plugin link nexaduo ./.claude-plugin` (pointed straight at the
   directory holding mARC's existing marketplace manifest) — measured
   (observed from installed binary). `link` is therefore not a
   marketplace-registration command; it presupposes the marketplace name is
   already known to `agy` some other way.

3. **`agy plugin import [gemini|claude]` converts already-installed
   plugins/extensions from Gemini CLI or Claude Code into `agy`'s local
   plugin registry** — measured (observed from installed binary; `agy plugin
   list` on this machine shows `{"name":"marc","source":"antigravity",
   "importedAt":"2026-07-09T18:46:46Z","components":["skills","agents"]}`, a
   leftover from prior local install/import activity). This is a local
   conversion utility, not a network/marketplace install path, and doesn't
   help a first-time user with nothing installed yet.

4. **Strings embedded in the `agy` binary indicate the marketplace feature is
   Google-internal-only** — measured (observed via `strings ~/.local/bin/agy
   | grep -i marketplace`, binary v1.1.4): the binary contains the literal
   string `"GetSkillMarketplaceLink is only available in Google environments"`
   next to an RPC method `GetSkillMarketplaceLink`, plus the error-format
   strings `"unknown marketplace: %s"` and `"unsupported marketplace type:
   %s"`. [inference from binary strings, not from any doc that explicitly
   states this] — combined with findings 1–2, this is strong circumstantial
   evidence that the shipped public CLI's marketplace resolution is gated to
   Google-internal environments, unlike Claude Code's `/plugin marketplace add
   <owner>/<repo>` or Copilot's `copilot plugin marketplace add`, which are
   general-purpose and GitHub-repo-driven.

5. **Official docs show only local-directory install; no marketplace/URL/GitHub
   syntax is documented publicly** — reported, official docs page,
   https://antigravity.google/docs/cli/plugins (fetched 2026-07-21). The
   install section is headed "Install a local or remote plugin," but per the
   fetched content "the accompanying example only demonstrates a local path"
   and "no GitHub URLs, remote install syntax, or marketplace functionality is
   documented on this page." The "remote" half of that heading is left
   unexplained in the visible doc — a documentation gap, not proof the feature
   doesn't exist, but it corroborates findings 1–2 that nothing publicly
   documented exercises a marketplace path successfully.

6. **mARC's existing `.claude-plugin/marketplace.json` uses the Claude Code
   marketplace schema** (`name`, `metadata`, `owner`, `plugins[].source` as a
   relative path) — measured (Read of
   `.claude-plugin/marketplace.json`). `agy`
   gave `unknown marketplace: nexaduo` even when invoked from the repo root
   where this file sits directly on disk, i.e. `agy` made no attempt to read
   it. It is a different, incompatible shape from anything `agy` is shown to
   consume.

7. **Community "agy marketplace" search hits are Claude Code plugins that
   wrap the `agy` CLI, not an Antigravity-native marketplace** — reported,
   WebSearch results 2026-07-21 (e.g.
   `github.com/simplybychris/antigravity-plugin-cc`,
   `github.com/Vit129/agy-plugin-cc`,
   `github.com/MarcosNahuel/antigravity-plugin-cc`). These install via Claude
   Code's own `/plugin marketplace add <repo>` to expose `agy` as a slash
   command *inside* Claude Code; they say nothing about `agy`'s own plugin
   marketplace. Flagged so this distinction isn't conflated in any downstream
   doc edit.

## Recommendation

No clone-free path exists today for the Antigravity harness. Keep the
current clone-based install/update docs in `README.md` and `docs/index.html`
exactly as they are; do **not** add or edit `.claude-plugin/marketplace.json`
for Antigravity, since `agy` doesn't consume that file and there is no
discovered way to register a custom marketplace name with `agy`. This is a
"no shipping change" outcome per the issue's own acceptance criteria: record
the conclusion on #151 and close it without a version bump.

## Implications for implementation

- **Files to change:** none. `README.md`'s Google Antigravity block
  (`git clone …` + `agy plugin install ./mARC/harnesses/antigravity/marc`) and
  `docs/index.html`'s Antigravity steps are already accurate and should be
  left as-is.
- **`.claude-plugin/marketplace.json`:** no change — it correctly serves
  Claude Code and Copilot only; it is not, and cannot currently be made, an
  `agy`-readable manifest.
- **`harnesses/antigravity/marc/plugin.json` / `COMPATIBILITY.md`:** no
  change required by this finding; `COMPATIBILITY.md` gains one sentence
  noting that `agy`'s `plugin@marketplace` install form was evaluated and
  found to require an internal Google marketplace registry not available to
  third-party repos as of `agy` v1.1.4 — a documentation nicety, not a
  functional fix.
- **No version bump / CHANGELOG entry** — per the issue's own acceptance
  criteria, a "no clone-free path" conclusion carries no shipping change.
- **If Google later documents/opens a public marketplace-registration
  command,** re-run this investigation: retest `agy plugin help` for a new
  subcommand (e.g. a hypothetical `marketplace add`), and re-check whether
  `agy plugin install <plugin>@<marketplace>` then resolves against a
  manifest at a known path (e.g. `.claude-plugin/marketplace.json` or an
  Antigravity-specific equivalent) before concluding a manifest addition is
  warranted.

## Coverage & gaps

- **Searched:** "agy plugin install marketplace Google Antigravity CLI docs",
  "antigravity.google docs plugin marketplace add".
- **Read:** installed `agy` v1.1.4 binary's help/error output for `agy
  --help`, `agy plugin help`, `agy plugin install --help`, `agy plugin link
  --help`, `agy plugin import --help`, `agy plugin list`, `agy plugin
  validate`; `strings` output of the binary filtered for "marketplace";
  official docs page https://antigravity.google/docs/cli/plugins (via
  WebFetch); WebSearch result summaries for community `agy`-related Claude
  Code plugins; repo files `.claude-plugin/marketplace.json`,
  `harnesses/antigravity/marc/plugin.json`,
  `harnesses/antigravity/marc/COMPATIBILITY.md`, and `README.md`'s
  Antigravity install/update blocks (via Read/Grep, not piped bash).
- Also ran non-destructive dry-run installs/links directly in the repo
  checkout (`agy plugin install marc@nexaduo`, `agy plugin install
  nexaduo/marc`, `agy plugin link nexaduo .`, `agy plugin link nexaduo
  ./.claude-plugin`, `agy plugin install ./harnesses/antigravity/marc`, `agy
  plugin validate ./harnesses/antigravity/marc`); no repo files or global
  config were modified — the only observed side effect was a local
  plugin-registry entry for `marc` from prior local-install activity.
- **NOT found:** any documented or working `agy` subcommand/flag that
  registers a new marketplace name against a URL or GitHub repo; any public
  official-docs page enumerating recognized marketplace names; concrete
  meaning of "remote plugin" in the docs heading "Install a local or remote
  plugin" (never got it to accept anything but a local directory in this
  session).
- **Staleness / bias notes:** official docs fetched today (2026-07-21) via
  WebFetch, which summarizes rather than dumps verbatim HTML — treat exact
  wording as approximate; this does not affect the load-bearing
  binary-observed behavior, which is the primary evidence. Community plugin
  repos found via search are third-party/unofficial and about a different
  mechanism (wrapping `agy` inside Claude Code) — explicitly not used as
  evidence for or against the native `agy` marketplace question (see finding
  7).
