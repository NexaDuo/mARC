# Plano de adaptação do mARC para Codex

## Objetivo

Adicionar o OpenAI Codex como harness suportado, preservando `core/` como fonte,
o despacho multi-harness, o isolamento por worktree e as barreiras de leitura e
revisão.

## Fases

1. Criar o harness compilado (`harnesses/codex/marc`) com manifesto, skill,
   agentes TOML e configuração de marketplace.
2. Adicionar `codex` ao dispatcher, com seleção explícita, detecção nativa,
   fallback e `codex exec --worktree`.
3. Implementar o renderer de hooks Codex (`SessionStart`, `PreToolUse`,
   `PostToolUse`, `Stop`) e documentar o trust obrigatório.
4. Tornar as instruções comuns semanticamente harness-neutral, deixando a
   sintaxe de ferramentas no `compile.json`.
5. Adicionar gates de CI para manifesto, agentes, sandbox, hooks e instalação
   fora do checkout; validar também um consumidor sem `team.toml`.
6. Atualizar versão, changelog e fazer revisão independente de segurança.

## Critérios de aceite

- `codex plugin marketplace add` e `codex plugin add marc@nexaduo` funcionam.
- `tech-lead` e os seis especialistas são descobertos pelo Codex.
- agentes de implementação usam `workspace-write`; revisores usam `read-only`.
- dispatch e worktree não dependem de flags específicas do Claude Code.
- hooks passam pelo schema Codex e são testados após trust explícito.
- CI prova compilação, paridade, instalação e execução fora deste repositório.

## Riscos acompanhados

- O isolamento sem Bash do `bulk-reader` ainda precisa ser comprovado no Codex.
- Telemetria baseada em transcript do Claude não deve ser reutilizada sem um
  adaptador para o formato do Codex.
- Hooks instalados são não-gerenciados e podem ser ignorados até `/hooks` confiar
  no hash atual.

## Pendências de implementação

- [x] Criar manifesto, `compile.json`, marketplace local e agentes TOML Codex.
- [x] Adicionar detecção, roteamento e comando inicial `codex exec --worktree`.
- [x] Adicionar renderer de hooks Codex ao compilador.
- [ ] Gerar os artefatos Codex com `python scripts/compile_prompts.py` e remover
  qualquer saída gerada manualmente que divergir do compilador.
- [ ] Validar o schema real de `hooks/hooks.json` no Codex e ajustar matcher,
  eventos e comandos Windows.
- [ ] Implementar hooks Codex seguros para `SessionStart`, `PreToolUse`,
  `PostToolUse` e `Stop`, incluindo trust e mensagens de erro.
- [ ] Adaptar `read-guard` para o JSON de entrada/saída do Codex e provar que
  bloqueia chamadas inadequadas.
- [ ] Confirmar se `bulk-reader` possui isolamento efetivo de execução no Codex;
  bloquear essa rota até a prova existir.
- [ ] Substituir a telemetria que pressupõe transcripts Claude por um adaptador
  Codex ou declarar explicitamente a capacidade como indisponível.
- [ ] Adicionar testes unitários de detecção, roteamento, renderer e fallback.
- [ ] Adicionar gates CI para manifesto, agentes, sandbox, hooks e instalação
  fora do checkout.
- [ ] Testar instalação com `codex plugin marketplace add` e `codex plugin add`
  em um consumidor sem `team.toml`.
- [ ] Documentar que o comando recebe a raiz do repositório (`codex plugin
  marketplace add .`), pois o Codex procura `.agents/plugins/marketplace.json`
  dentro da raiz selecionada.
- [ ] Atualizar `docs/ARCHITECTURE.md` para refletir o quarto harness.
- [ ] Atualizar versão, `CHANGELOG.md` e executar revisão independente `@sec`.
