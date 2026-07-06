# Research brief: where should durable team artifacts live, and may read-only agents write them?

> **Artifact record** — type: `brief` · produced by: the `@research` specialist
> (mARC team) · date: 2026-07-06 · motivating issue:
> [NexaDuo/mARC#46](https://github.com/NexaDuo/mARC/issues/46) · original:
> [issue comment](https://github.com/NexaDuo/mARC/issues/46#issuecomment-4893879861)
> · outcome: the PEF decision recorded in
> [this comment](https://github.com/NexaDuo/mARC/issues/46#issuecomment-4894129037)
> (location `docs/marc/`, public by construction; read-only agents comment-only;
> operator materializes via reviewed PR).

**TL;DR** — The ADR canon strongly supports keeping decision/research artifacts *in the repo, versioned* (ThoughtWorks: Adopt) rather than only in issue threads. Everything under `docs/` on the Pages source branch is published publicly on push, so `docs/team/`–style paths make briefs public web content by construction; a non-published root folder (dot-dir or plain `decisions/`-style dir) avoids that. Security guidance (OWASP LLM06, GitHub token hardening, GitHub Security Lab) uniformly favors default read-only + human-gated escalation over standing write carve-outs, and the dominant bot model (Dependabot, Renovate default) lands changes via PR, not direct commit. Confidence: high on (a) and (c); moderate on (b) — no direct public evidence on AI-reviewer write carve-outs specifically, so that part is principle-based inference.

## Findings

### A. Persisting decision artifacts in-repo (ADR canon)

- **The founding ADR practice keeps decision records in the project repository, in a dedicated versioned folder with sequential numbering** [reported] — Michael Nygard, "Documenting Architecture Decisions", 2011-11-15, https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions
  > "We will keep ADRs in the project repository under doc/arch/adr-NNN.md" … "ADRs will be numbered sequentially and monotonically. Numbers will not be reused."
- **Superseded decisions are retained, not deleted** [reported] — same source.
  > "If a decision is reversed, we will keep the old one around, but mark it as superseded."
- **ThoughtWorks Tech Radar rates lightweight ADRs "Adopt" and explicitly prefers source control over a wiki/website** [reported] — Tech Radar, technique promoted to Adopt May 2018, https://www.thoughtworks.com/en-us/radar/techniques/lightweight-architecture-decision-records
  > "We recommend storing these details in source control, instead of a wiki or website"
- **Common folder names in the ADR community are plain, visible directories (`adr/`, `decisions/`)** [reported] — joelparkerhenderson/architecture-decision-record (widely-referenced ADR compendium), https://github.com/joelparkerhenderson/architecture-decision-record
  > "Create a directory for ADR files: … $ mkdir adr" … "When some teams use the directory name \"decisions\", then it's as if a light bulb turns on"
- **AI-tooling precedent: checked-in dot-directories are the established home for tool/team state** [reported] — Claude Code settings docs (current), https://code.claude.com/docs/en/settings
  > "`.claude/settings.json` for settings that are checked into source control and shared with your team"

### B. GitHub Pages exposure of `docs/`

- **With `/docs` as the publishing source, every push publishes that folder's contents publicly — even if the repo is private** [reported] — GitHub Docs, "Configuring a publishing source for your GitHub Pages site" (current), https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site
  > "Whenever changes are pushed to the source branch, the changes in the source folder will be published to your GitHub Pages site."
  The page also warns Pages sites are publicly accessible on the internet even when the underlying repository is private.
- **Jekyll (the default Pages branch build) excludes dot-/underscore-prefixed paths from output** [reported] — Jekyll docs, "Directory structure" (current), https://jekyllrb.com/docs/structure/
  > "every file or directory beginning with the following characters: `.`, `_`, `#` or `~` … will not be included in the `destination` folder."
  [inference — my own reasoning] This repo's `docs/` has no `.nojekyll`/`_config.yml`, so a dot-subdir under `docs/` would *currently* be excluded from the site — but that protection is fragile: adding `.nojekyll` later (a common fix for static assets) silently flips such paths public. Also note `docs/ARCHITECTURE.md` and `docs/team.config.example` are already publicly served content today.

### C. Least-privilege write policies for agents/bots

- **OWASP names excessive permissions as a root cause of Excessive Agency and prescribes minimum-necessary grants** [reported] — OWASP GenAI, LLM06:2025 Excessive Agency (updated 2025), https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
  > "Limit the permissions that LLM extensions are granted to other systems to the minimum necessary"
  Its "excessive permissions" example is precisely a read-only component whose identity also holds write rights; it further recommends human approval for high-impact operations.
- **GitHub's own hardening guidance: default the automation token to read-only, elevate narrowly and temporarily** [reported] — GitHub Docs, "Security hardening for GitHub Actions" (current), https://docs.github.com/en/actions/security-for-github-actions/security-guides/security-hardening-for-github-actions
  > "You should therefore make sure that the `GITHUB_TOKEN` is granted the minimum required permissions."
  It calls setting the default to read-only for repo contents "good security practice", raising permissions per-job only where needed.
- **Documented failure mode of over-privileged automation: write-context workflows processing untrusted input get hijacked ("pwn requests")** [reported] — GitHub Security Lab, "Preventing pwn requests", https://securitylab.github.com/resources/github-actions-preventing-pwn-requests/
  > malicious PR authors risk "being able to obtain repository write permissions or stealing repository secrets."
  Recommended pattern: process untrusted input in an *unprivileged* context and hand results to a privileged step only when write access is truly needed — structurally the same as "read-only researcher produces text; privileged operator commits it." [inference — the mapping to our agents is my reasoning]

### D. PR vs direct commit for bot-produced artifacts

- **Dependabot's model: all changes land as pull requests** [reported] — GitHub Docs, "About Dependabot version updates" (current), https://docs.github.com/en/code-security/dependabot/dependabot-version-updates/about-dependabot-version-updates
  > "it raises a pull request to update the manifest"
- **Renovate defaults to PR-based landing; direct-to-branch is an opt-in that requires passing status checks and is blocked by branch protection** [reported] — Renovate docs, "Automerge" (current), https://docs.renovatebot.com/key-concepts/automerge/
  > "Renovate will not automerge until it sees passing status checks / check runs for the branch." … if branch protection blocks pushes, "you should stick with the default PR-based automerging instead."
  [inference — my own reasoning] Renovate's direct-commit mode is gated on automated tests as the review substitute. Prose briefs/ADRs have no test gate, so the analogous safety gate for them is PR review.

## Implications for the decision

1. **Location/name** — Evidence supports an in-repo, versioned, plainly-named folder (Nygard `doc/arch/`, community `adr/` / `decisions/`, ThoughtWorks "source control, not a wiki"). Evidence is decisive that `docs/team/` or `docs/marc/` = public web content on this repo (Pages publishes all of `/docs` on push). A root-level dot-dir (`.marc/`) matches the `.claude/` precedent and is never Pages-served; the trade-off (my inference, no direct evidence) is discoverability — the ADR community deliberately uses *visible* names like `decisions`. Evidence is silent on `team` vs `marc` naming specifically. Do not rely on Jekyll's dot-exclusion inside `docs/` — it's one `.nojekyll` away from public.
2. **What belongs there** — The canon covers decision records (ADRs): durable, numbered, immutable-but-supersedable, stored with the code. Research briefs and option studies are the inputs to such records; ThoughtWorks' "not a wiki or website" logic applies to anything the team must find later. Ephemeral back-and-forth has no in-repo precedent in these sources — issue comments remain the right home for discussion; the *compiled* artifact is what gets filed. Board snapshots: no public evidence either way.
3. **Write policy** — All three security sources point the same way: read-only by default, minimum-necessary grants, human approval for state-changing actions. A standing write carve-out for `@sec`/`@research` is exactly OWASP's "excessive permissions" anti-pattern in miniature; the pwn-requests pattern (unprivileged producer → privileged committer) endorses the current model of comment-only agents + `@techlead` committing the file. If the team still wants a carve-out, the evidence supports making it path-scoped and narrow (Claude Code permission rules support this), but no source demonstrates that a scoped write for agents that ingest untrusted web content is safe — that risk assessment stays with `@sec`. Note `@research` in particular fetches arbitrary web pages (prompt-injection surface), which strengthens the case against giving it any write path.
4. **Landing process** — The dominant, documented bot model (Dependabot always; Renovate by default) lands via PR. The only sanctioned direct-commit precedent found (Renovate branch automerge) is conditional on passing automated checks and defers to branch protection. For prose artifacts with no automated gate, PR review is the evidence-aligned choice.

## Coverage & gaps

- **Searched/fetched:** ADR canon (Nygard, adr.github.io, joelparkerhenderson repo, ThoughtWorks Radar), GitHub Pages publishing-source docs, Jekyll build-exclusion docs, GitHub Actions security hardening, OWASP LLM06:2025 Excessive Agency, GitHub Security Lab pwn-requests, Dependabot docs, Renovate automerge docs, Claude Code settings docs.
- **Read:** 12 sources fetched and read this session (all cited above).
- **NOT found:** (1) any public post-mortem specifically about an over-privileged *AI agent* writing files to a repo (the pwn-requests write-token abuse is the closest documented analogue); (2) any published convention from Cursor/aider/OpenHands specifically for *agent-authored durable artifacts* folders (their dot-dirs hold config/rules, not outputs) — I did not fetch those docs within the timebox; (3) any evidence comparing `docs/team` vs `docs/marc` style naming; (4) measured data of any kind — every finding here is **reported** (docs/conventions), none is **measured**.
- **Staleness/bias notes:** Nygard (2011) and the Radar entry (2018) are old but remain the acknowledged canon and are consistent with current tooling. GitHub, Renovate, Jekyll, OWASP and Claude Code sources are vendor/project-authored documentation — authoritative for their own behavior, but vendor-authored. Several pages were re-fetched due to a local output-compression filter; all quotes above come from successfully fetched content.
