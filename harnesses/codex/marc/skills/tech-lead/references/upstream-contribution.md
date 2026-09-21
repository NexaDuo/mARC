# Upstream Contribution Guide

Proposing generalizable, product-level process improvements upstream is handled through a two-tier model. Both tiers are opt-in. They do not edit the plugin from a consumer repository without explicit human consent.

## Tier 1: Default, local (every repo)
Lessons land first in the local, editable targets owned by the operator. These include the repository`'`s AGENTS.md, the team.toml file, or the personal buffer. This is the only automatic path and where every lesson goes initially. In a consumer repository, Tier 1 is the entire path unless the human explicitly escalates. You must not edit the plugin`'`s own skill or agent files, and you must not open any autonomous upstream pull requests.

## Tier 2: Opt-in upstream contribution (the sanctioned channel, issue #22)
When a lesson looks generalizable to the product and would help every mARC user, you may offer to propose it upstream. This is an explicit, consented escalation. Nothing leaves the user`'`s repository without their approval. Follow the flow in order, never skipping a step:

1. **Land it locally first (Tier 1):** The lesson is captured locally regardless of whether it goes upstream. Upstream is additive and never a replacement.
2. **Offer, do not act:** Surface a one-line offer, for example: "This looks generally useful. Want me to propose it upstream to the mARC plugin as a field-lesson PR?" Do nothing further without an explicit "yes" from the human.
3. **On explicit yes: sanitize and generalize:** Produce the change as a generalized diff against the plugin`'`s skill or agent prose along with a pull request body. Send the lesson, not the raw context. Scrub every local specific: repository, organization, and user names or slugs, absolute paths, hostnames, IDs, secrets, and any consumer-repository domain details. If it cannot be generalized without leaking local context, keep it Tier 1 only.
4. **Show the human the exact artifacts for approval:** Display the full diff and the full pull request body. Get explicit approval of that text before submitting. The human must approve the exact bytes that leave the repository.
5. **Submit via a fork-based PR:** Only after approval, open a fork-based pull request against the plugin`'`s upstream repository under the user`'`s own GitHub identity. You can run `gh repo fork`, branch, and then `gh pr create` with the label `field-lesson`. Resolve the upstream repository at runtime using context detection or `gh`. Do not hardcode any organization or repository slug. The pull request is a proposal reviewed by CI, `@sec`, and a human maintainer. It is never auto-merged.

This flow is never autonomous at any step. The offer needs a yes, and the submission needs approval of the exact diff and body. This remains consistent with the fail-closed gate: in a consumer repository, the upstream path is only this human-approved opt-in offer, never an autonomous upstream pull request. In the plugin source repository (dogfooding), the same lesson is just an ordinary in-repo edit and pull request.

## Who may contribute (org-members pilot)
For now, the upstream channel is a pilot open to mARC organization members only. The skill cannot verify organization membership, so it does not enforce eligibility. It sets an expectation: if you are not an organization member, keep the lesson local (Tier 1) and, if you wish, share it as an issue. Widening the pilot to anyone via fork is a scheduled decision, tracked in issue #25 with a checkpoint around 2026-07-17.
