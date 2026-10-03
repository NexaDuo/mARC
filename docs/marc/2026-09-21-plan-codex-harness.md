# Plano de adaptação do mARC para Codex

## Estado em 2026-10-02 — PR #330

Suporte inicial: instalação, skills e despacho de escrita. `core/` segue fonte
única; não declaramos paridade completa de hooks, agentes ou telemetria.

## Implementado

- Manifesto, marketplace raiz, renderer e descritores TOML compilados do core.
- Escrita: `codex exec --worktree --sandbox workspace-write`.
- Leitura/revisão/segurança/pesquisa/bulk-reader: Claude Code obrigatório conforme
  #323; overrides Codex redirecionados, ausência de Claude falha sem executar.
- Read-guard Codex desativado até comprovar contrato real de deny.
- Sentinel/telemetria Codex indisponíveis com diagnóstico antes de I/O;
  `--harness claude-code` preserva uso intencional de dados Claude.
- Entrada tech-lead reduzida com referências sob demanda, origens preservadas e
  limite conservador de 8 KB no repositório (não um limite oficial do Codex).
- Gates de estrutura, versão, marketplace, paridade e regressões negativas.
- Smoke com CLI 0.156.1: marketplace/add/list reais em consumidor temporário sem
  team.toml e com CODEX_HOME isolado; helpers da cópia instalada, sem modelo.
- CI fixa versão e integridade via npm lockfile; README usa `codex plugin add`.

## Ainda não comprovado

- Descoberta automática de agentes TOML; arquivos instalados são templates.
- Eventos reais Codex com trust, payloads, matchers e comandos Windows.
- Contrato de bloqueio read-guard e isolamento sem execução do bulk-reader.
- Adaptador validado de transcripts Codex para habilitar telemetria.
- CI remoto no HEAD final, reviews independentes, merge e release completos.

Instalação e helpers não comprovam esses contratos. A raiz passada ao marketplace
contém `.agents/plugins/marketplace.json`. Caches do usuário são imutáveis.
