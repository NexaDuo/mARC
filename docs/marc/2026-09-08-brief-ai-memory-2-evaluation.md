# Research brief: architectural evaluation of `akitaonrails/ai-memory` 2.0 and implications for mARC

- **Type:** research brief
- **Date:** 2026-09-08
- **Attribution:** produced by `@research` for the `@techlead` operator and mARC team, analyzing external release [AI-MEMORY 2.0](https://akitaonrails.com/2026/09/02/ai-memory-2-0-melhor-sistema-memoria-agentes-e-times/). Materialized per PEF policy (#46).
- **Status:** accepted — verdict CONFIRM (reaffirm #175 rejection of mandatory daemon dependency; adopt OKF metadata, static typed-relation checks in CI, slot isolation, and opt-in MCP bridge).
- **Related Records:**
  - [`2026-07-29-decision-ai-memory-rejected.md`](2026-07-29-decision-ai-memory-rejected.md) (#175, #176)
  - [`2026-07-24-decision-distribution-channel-marketplace-only.md`](2026-07-24-decision-distribution-channel-marketplace-only.md) (#161)
  - [`2026-08-25-brief-concurrent-operator-coordination.md`](2026-08-25-brief-concurrent-operator-coordination.md) (#205)

---

## TL;DR

The release of **AI-Memory 2.0** (published 2026-09-02 by Fabio Akita) matures the project into an open-format, single-writer multi-agent memory server. Key technical shifts from v1.x:
1. Native on-disk wiki format migrated to Google's **Open Knowledge Format (OKF v0.2)** — eliminating proprietary file formats;
2. In-process embeddings in pure Rust via **Candle** (`all-MiniLM-L6-v2`) — eliminating external API calls, Ollama sidecars, and brittle C++ bindings;
3. **Single-writer serialization** with backpressure and version chaining — preventing concurrent write corruption across multiple agent CLIs;
4. Formal **handoff protocol with single-owner baton passing** and personal working-slot isolation in team mode;
5. **Typed semantic links** (`causes`, `fixes`, `contradicts`, `supersedes`) with static, zero-LLM contradiction validation.

**Verdict for mARC:**
- **Reaffirmation of Decision #175:** mARC does **not** adopt AI-Memory 2.0 as a mandatory dependency or core distribution requirement. The marketplace-only constraint (#161), zero-config invariant, and mandatory PR-review gate for durable governance memories (`docs/marc/`) remain incompatible with an autonomous write-hook daemon.
- **Architectural Borrowing (Zero-Dependency):** mARC can adopt four core patterns natively using Python stdlib and Git conventions:
  1. Standardize `docs/marc/` and memory artifacts with OKF-compliant YAML frontmatter;
  2. Extend `check_rule_origin.py` to validate static typed relations (`supersedes`, `contradicts`) at CI compile-time;
  3. Formalize operator baton handoffs in `tech-lead/SKILL.md`;
  4. Provide an opt-in, read-only MCP bridge for developers who already run AI-Memory locally.

---

## 1. What AI-Memory 2.0 Delivers

### 1.1 Open Knowledge Format (OKF)
The underlying storage is no longer an internal markdown dialect. Every memory page is natively a valid OKF document (Google 2026 open specification) with standardized metadata. No export translation layer exists: the wiki files on disk *are* the OKF bundle. Tooling interoperability (`grep`, Obsidian, Git, `ai-memory export-okf`) is native. Migration from v1.x includes an automated, checksum-verified backup before format transformation.

### 1.2 In-Process Local Embeddings (Rust + Candle)
Version 1.x required either paid third-party embedding APIs or an external Ollama instance. Version 2.0 embeds the `all-MiniLM-L6-v2` model directly inside the Rust process using Hugging Face's **Candle** framework.
- Download is an automatic, checksum-pinned ~87 MB asset on first run.
- LongMemEval-S benchmark `hit@5` increased from 0.617 (FTS5 text-only) to **0.779** (hybrid search).
- Avoids native C++ runtime binding crashes that afflicted competing solutions (e.g. *agentmemory*).

### 1.3 Concurrency & Single-Writer Architecture
To support concurrent harnesses (e.g. Claude Code, Codex, Antigravity) pointing at the same project:
- Project identity is derived from the Git session checkout directory, independent of absolute paths across machines.
- All writes are queued through a single-writer actor with backpressure, appending revision chains rather than overwriting pages in-place.
- Identical re-writes are deduplicated.

### 1.4 Baton Handoff Protocol & Team Slot Isolation
- Multi-agent handoff operates as a typed, single-consumer baton: exactly one session accepts the handoff lease, preventing race conditions or session theft.
- Team deployments over HTTPS/SQLite isolate personal "current work" slots from team-wide opening briefings, avoiding context pollution while preserving shared knowledge and audit attribution ("edited by X").

### 1.5 Typed Links & Zero-LLM Contradiction Detection
Pages support explicit semantic relationships (`causes`, `fixes`, `contradicts`, `supersedes`). The server validates structural consistency and contradiction graphs statically without spending LLM tokens. Temporal queries (`as_of: YYYY-MM-DD`) allow reconstructing historical knowledge states.

---

## 2. Re-evaluating mARC Rejection Reasons (#175)

| Rejection Criterion (#175) | Status in v2.0 | mARC Architectural Impact |
| :--- | :---: | :--- |
| **Distribution Channel (Marketplace-only, #161)** | **Still Fails (Hard Gate)** | AI-Memory distributes via Docker, AUR, Homebrew, and release binaries. mARC is distributed strictly via Claude Code / Antigravity plugin marketplaces. Requiring an OS-level daemon breaks zero-config onboarding. |
| **Autonomous Unreviewed Writes vs PR Gates** | **Still Fails (Hard Gate)** | AI-Memory writes autonomously during agent execution. In mARC, durable memories govern team invariants and MUST pass `@sec` and `@rev` review via PR before landing in `docs/marc/`. Post-facto attribution does not replace pre-persistence review. |
| **Daemon Attack Surface & Operational Overhead** | **Improved, but Still Present** | Single-writer and Rust/Candle improve stability, but a persistent local HTTP/MCP server remains an external process dependency. |

**Conclusion:** Neither revisit condition from Decision #175 is met. Decision #175 stands.

---

## 3. High-Value Patterns Borrowed by mARC

mARC implements the strengths of AI-Memory 2.0 without adopting the daemon:

1. **OKF Metadata in `docs/marc/`:**
   Migrate artifact frontmatter to align with OKF v0.2 specifications, enabling zero-effort integration with Obsidian and external indexing tools.
2. **Static Relation CI Gate (`check_rule_origin.py`):**
   Extend mARC's deterministic rule-origin gate to parse and enforce typed relations (`<!-- relation: supersedes #NN -->`, `<!-- relation: contradicts #NN -->`) in governed rule fences. Zero token cost, millisecond execution in CI Tier 1.
3. **Explicit Operator Baton Passing:**
   Complement mARC's concurrent claim protocol (`## @techlead claim`, #213) with a formal `## @techlead handoff to <operator>` marker in `tech-lead/SKILL.md`.
4. **Plug-and-Play MCP Bridge (Opt-in):**
   If an operator environment already has AI-Memory running locally, allow `@research` and `@dev` to query it as an optional read source, while keeping all durable writes PR-gated in `docs/marc/`.

---

## Sources

- [Akita on Rails: AI-MEMORY 2.0 - o melhor sistema de memória para agentes e times (2026-09-02)](https://akitaonrails.com/2026/09/02/ai-memory-2-0-melhor-sistema-memoria-agentes-e-times/)
- [akitaonrails/ai-memory GitHub Repository (v2.0.0)](https://github.com/akitaonrails/ai-memory)
- [`docs/marc/2026-07-29-decision-ai-memory-rejected.md`](2026-07-29-decision-ai-memory-rejected.md)
- [`docs/marc/2026-07-24-decision-distribution-channel-marketplace-only.md`](2026-07-24-decision-distribution-channel-marketplace-only.md)
- [`core/scripts/check_rule_origin.py`](../../core/scripts/check_rule_origin.py)
