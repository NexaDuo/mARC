# Decision record: reaffirmation of Decision #175 — mARC does not adopt `akitaonrails/ai-memory` 2.0 as a dependency

- **Type:** decision record
- **Date:** 2026-09-08
- **Attribution:** operator + user decision, based on the `@research` brief in [`2026-09-08-brief-ai-memory-2-evaluation.md`](2026-09-08-brief-ai-memory-2-evaluation.md). Materialized per PEF policy (#46).
- **Status:** accepted (docs-only; no plugin version bump)
- **Supersedes:** None (reaffirms and extends [`2026-07-29-decision-ai-memory-rejected.md`](2026-07-29-decision-ai-memory-rejected.md))

---

## Decision

mARC **reaffirms Decision #175**: it does **not** adopt `akitaonrails/ai-memory` (v2.0 or subsequent versions) as a core dependency, daemon, or installer requirement. mARC preserves its foundational architectural invariants:
1. **Marketplace-only distribution** (#161) with zero-config runtime discovery;
2. **PR-gated durable memory** in `docs/marc/` for team rules and governance invariants (pre-merge review by `@sec` and `@rev`);
3. **Daemon-free operations** relying on Git, session auto-memory, and GitHub Projects.

---

## Context and Re-evaluation of v2.0

On 2026-09-02, `akitaonrails/ai-memory` released version 2.0.0, introducing Google's Open Knowledge Format (OKF v0.2), in-process Candle Rust embeddings (`all-MiniLM-L6-v2`), single-writer serialization with backpressure, baton-passing handoff, team slot isolation, and zero-LLM typed relationship checks.

A study was commissioned to determine whether v2.0 satisfied the revisit conditions established in Decision #175.

### Revisit Conditions Check

| Condition from #175 | Evaluation against v2.0 | Result |
| :--- | :--- | :---: |
| 1. Marketplace-only distribution (#161) revisited on its own merits | The marketplace remains mARC's sole distribution channel. AI-Memory requires OS-level package managers (AUR, Homebrew, Docker, release binaries). | **NOT MET** |
| 2. AI-Memory installable via marketplace with PR-gated write path | AI-Memory remains an external HTTP/MCP daemon with autonomous mid-session writes via hooks. | **NOT MET** |

Because neither revisit condition was met, AI-Memory 2.0 cannot be adopted into mARC core.

---

## What mARC Adopts from AI-Memory 2.0 (Zero-Dependency)

While rejecting the daemon, mARC incorporates the following patterns natively:

1. **OKF Metadata Compliance:** Future artifacts in `docs/marc/` will align with OKF frontmatter conventions for broad interoperability with external knowledge tools.
2. **Static Relation CI Gate:** Extend `core/scripts/check_rule_origin.py` to parse and statically check relational rule links (`supersedes`, `contradicts`, `fixes`) during Tier 1 CI without LLM token cost.
3. **Operator Baton Passing:** Formalize explicit baton handoff syntax (`## @techlead handoff to <operator>`) within `tech-lead/SKILL.md` to enhance cross-operator handoffs.
4. **Opt-in MCP Read Bridge:** Allow operators who already run AI-Memory in their local environment to optionally query it during `@research` / `@dev` exploration, with all persistent writes strictly routed through PRs in `docs/marc/`.

---

## Sources

- [`docs/marc/2026-09-08-brief-ai-memory-2-evaluation.md`](2026-09-08-brief-ai-memory-2-evaluation.md)
- [`docs/marc/2026-07-29-decision-ai-memory-rejected.md`](2026-07-29-decision-ai-memory-rejected.md) (#175, #176)
- [`docs/marc/2026-07-24-decision-distribution-channel-marketplace-only.md`](2026-07-24-decision-distribution-channel-marketplace-only.md) (#161)
- [Akita on Rails: AI-MEMORY 2.0 (2026-09-02)](https://akitaonrails.com/2026/09/02/ai-memory-2-0-melhor-sistema-memoria-agentes-e-times/)
