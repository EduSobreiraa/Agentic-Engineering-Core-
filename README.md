# Agentic Engineering Core

O Agentic Engineering Core (AEC) é um projeto Python para coordenar trabalho de engenharia entre agentes especializados, verificações determinísticas e aprovação humana. A Fase 1 implementa o ACP v0.1; a Fase 2 acrescenta execução por adapters; a Fase 3 acrescenta workflows declarativos e persistentes; a Fase 4 acrescenta o ciclo adversarial com worktrees e QA independente. A Fase 5 acrescenta documentação determinística das execuções.

## Começar

Use Python 3.11 ou mais recente. No Fedora, com `uv` instalado:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
uv run python -m aec.simulation.agents
uv run pytest tests/workflows/test_engine.py -q
```

O ambiente de desenvolvimento usa ferramentas locais e não exige `OPENAI_API_KEY`. A Fase 2 integra o executável Codex CLI e usa a autenticação já disponível. O teste padrão usa um executável falso e não consome a assinatura.

Para executar a demonstração real com dois agentes e persistir o histórico:

```bash
uv run python -m aec.execution.demo --database /tmp/aec-phase2.sqlite --workspace /tmp/aec-phase2-work
```

Esse comando inicia dois turnos Codex read-only e consome capacidade da assinatura. Consulte [execução](docs/execution/EXECUTION_LAYER.md), [contratos](docs/execution/EXECUTOR_CONTRACT.md) e [testes](docs/execution/TESTING.md).

## Workflow Engine (Fase 3)

Execute o cenário declarativo com adapter simulado e aprovação local explícita:

```bash
uv run python -m aec.workflows.demo --approve
```

O comando imprime o `run_id` e o estado final. Sem `--approve`, ele para em `WAITING_APPROVAL`. Use `--database` e `--workspace` para escolher os caminhos locais. `--real-codex` é opt-in: executa dois turnos Codex read-only e consome capacidade da assinatura. A suíte padrão nunca usa essa opção. Veja o [esquema e os limites](docs/workflows/WORKFLOW_ENGINE.md).

## Documentação de execuções (Fase 5)

O `DocumentationWorker` recebe pedidos ACP do orquestrador e produz relatórios Markdown a partir das tabelas existentes de engenharia, workflow e mensagens. Os documentos históricos têm ID, referência e SHA-256 persistidos no SQLite. A geração padrão é determinística e não chama o Codex CLI. Consulte [uso](docs/documentation-worker/USAGE.md), [contratos](docs/documentation-worker/CONTRACTS.md), [formato](docs/documentation-worker/REPORT_FORMAT.md), [auditoria inicial](docs/documentation-worker/AUDIT.md) e [verificação](docs/documentation-worker/VERIFICATION.md).

## Estrutura

```text
src/aec/                  pacote Python
tests/                    verificações do pacote
docs/architecture/        arquitetura, componentes e decisões
docs/protocols/            contratos e limites do ACP v0.1
docs/workflows/            contratos e limites dos workflows
docs/engineering/          ciclo adversarial, papéis, gates e aprovação
docs/documentation-worker/ worker documental, contratos e uso
docs/ROADMAP.md           tarefas e critérios por fase
```

O [roadmap](docs/ROADMAP.md) registra o progresso. A [arquitetura](docs/architecture/ARCHITECTURE.md) define limites e invariantes; [componentes](docs/architecture/COMPONENTS.md) distribui responsabilidades; [decisões](docs/architecture/DECISIONS.md) registra escolhas aceitas. A [especificação ACP](docs/protocols/ACP_SPEC.md) descreve o contrato implementado. As [instruções de contribuição](AGENTS.md) valem também para agentes que trabalham neste repositório.

## Governança metodológica

A Fase 6 inicial lê históricos tipados, calcula métricas, gera RFCs pelo Documentation Worker da Fase 5, avalia propostas e publica versões após decisão humana local. Execute `uv run pytest -q tests/scenarios/test_methodology_evolution.py` para o cenário controlado. Veja o [diagnóstico e os limites](docs/methodology-worker/ARCHITECTURE.md). O controlador adversarial pode selecionar uma versão publicada compatível, incluindo um gate antes do QA.

O fechamento da Fase 6 acrescenta [benchmark local executado](docs/methodology-worker/EVALUATION.md), testes entre processos e [política explícita de confiança do projeto](docs/execution/SECURITY.md). O benchmark usa agentes roteirizados e não mede melhoria causal nem iterações de revisão; projetos externos não confiáveis exigem sandbox de sistema operacional antes de execução.

## Expansão metodológica (Fase 7 local supervisionada)

TDD, auditoria estática, decisão adversarial e Arena têm definições versionadas para o Workflow Engine. Novas execuções publicadas preservam requisitos, critérios e referências de artefatos após reinício. As versões `implementation-arena@1.0.1`, `tdd-adversarial@1.0.1`, `security-audit@1.0.3` e `decision-adversarial@1.0.1` foram publicadas no Registry local após ensaios com Codex real e aprovação metodológica local. Esse banco operacional não acompanha o Git; uma instalação nova precisa publicar suas versões pelo processo de governança. A Arena sequencial mede tempo e memória de uma carga declarada, mantém empate quando as medições se sobrepõem e encaminha um único vencedor de gate ao QA. A auditoria `1.0.2` segue bloqueada. Aprovações de runs e merges continuam separados da publicação metodológica. O Router escolhe somente versões publicadas e bloqueia dependências ausentes. Consulte a [matriz de prontidão, cenários e limites](docs/methodologies/PHASE_7.md). Código não confiável permanece bloqueado sem sandbox de sistema operacional.

Para projetos locais confiáveis em Linux, o adapter Codex oferece supervisão opcional por scope do `systemd --user` (`supervise_with_systemd=True`), incluindo filhos que saem do PGID. Há também um gateway de aprovação por socket Unix que deriva o UID do operador. Sua fronteira de identidade requer serviço, operador e agentes com permissões separadas; essa implantação ainda não foi feita. Consulte [execução e recuperação](docs/execution/EXECUTION_LAYER.md) e [governança local](docs/methodology-worker/GOVERNANCE.md). Nenhum desses controles fornece sandbox ou autenticação remota.

## Operação local (Fase 8 parcial)

A CLI `aec` consulta um banco existente sem alterá-lo. Por exemplo:

```bash
uv run aec --database /tmp/aec.sqlite status
uv run aec --database /tmp/aec.sqlite runs timeline RUN_ID
uv run aec --database /tmp/aec.sqlite --json processes inspect RUN_ID
```

Ela também lista runs, agentes, aprovações pendentes e metodologias, inspeciona
runs e resume métricas reportadas. `ExecutionBudget` permite limitar invocações
por run. `CodexSettings` permite configurar modelo e esforço sem alterar a
configuração global do Codex. Skills locais aprovadas podem ser vinculadas a um
run por digest; o adapter Orca é opcional e somente leitura. Consulte os
[comandos, evidências e limites da Fase 8](docs/operations/PHASE_8.md).

O [Marco A de hardening operacional](docs/operations/OPS_HARDENING.md) verificou
uma política de limites versão 2, orçamento e concorrência entre processos e
um turno Codex real com modelo explícito. A Fase 8 permanece parcial.

O [Marco B de integração](docs/operations/INTEGRATED_WORKSPACE.md) comprovou
`skill.enable` pelo gateway até o pedido recebido pelo Codex e executou três
pares de benchmark real, ainda inconclusivos. A interface Orca instalada não
oferece contrato documentado para supervisionar runs AEC externos.
O [aceite final da Fase 8](docs/operations/PHASE_8_ACCEPTANCE.md) classifica a
entrega como PARTIAL e lista os critérios cumpridos e as pendências transferidas.

## Limites atuais

O ACP v0.1 permanece independente de executores. A Execution Layer inicia processos e guarda estados. O Workflow Engine coordena grafos acíclicos; o ciclo adversarial controla repetições limitadas usando seus workflows. Execute `uv run pytest -q tests/scenarios/test_adversarial_loop.py` para o cenário simulado. Veja [o ciclo e suas limitações](docs/engineering/ADVERSARIAL_LOOP.md) e [a execução com Codex real](docs/engineering/REAL_CODEX_EXPERIMENT.md). Handles e aprovação local não autenticam usuários entre máquinas. Reinícios não repetem turnos externos de resultado incerto. Managers e controllers usam travas por execução no sistema de arquivos local. A Fase 5 gera relatórios sem LLM; propostas técnicas exigem revisão antes de alterar a documentação do projeto de destino.
