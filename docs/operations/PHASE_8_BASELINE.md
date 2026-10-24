# Baseline da Fase 8

Registro de 2026-10-23, antes das alterações da Fase 8. Esta matriz descreve o
checkout local, não presume que bancos operacionais externos estejam disponíveis.

## Verificação inicial

| Verificação | Resultado |
| --- | --- |
| `uv run --extra dev pytest -q` | 208 passed, 12 skipped |
| `uv run --extra dev ruff check .` | All checks passed |
| `uv run --extra dev mypy` | Success, 83 source files |
| Codex CLI | `codex-cli 0.163.0`; `codex exec` aceita `--model` e `--config` |
| Dependências de execução | `pydantic>=2,<3`, `PyYAML>=6,<7` |
| Dependências de desenvolvimento | mypy, pytest, ruff, types-PyYAML |

Os testes usam o ambiente Python 3.14.8 criado por `uv`. O cache foi colocado em
`/tmp/aec-phase8-uv-cache`, pois o cache padrão não era gravável. As dependências
de desenvolvimento precisaram ser baixadas após autorização de execução fora do
sandbox. A suíte padrão não consome turnos Codex reais.

## Matriz de prontidão

| Componente | Estado | Evidência e limite |
| --- | --- | --- |
| ACP | READY | `messages`, `deliveries` e `events` persistidos no SQLite; identidade e correlação no envelope. |
| Workflow Engine | READY | Definição e hash fixados por run; `workflow_events`, passos, tentativas e aprovações persistidos. |
| Execution Manager | PARTIALLY_READY | Estados e identidade de processos persistidos; a recuperação conservadora mantém quarentena, mas não garante inspeção de todos os descendentes sem scope confiável. |
| Documentation Worker | READY | Relatórios históricos tipados e hash persistidos; geração padrão determinística. |
| Methodology Registry | PARTIALLY_READY | Código e testes de publicação supervisionada presentes. O banco operacional citado na documentação da Fase 7 não está neste checkout. |
| Quality Gates e AEC-SEC-001 | PARTIALLY_READY | Testes de integridade aprovados com fixtures. O ensaio real documentado terminou em `WAITING_APPROVAL`. |
| AEC-RES-002 | PARTIALLY_READY | Testes de recuperação e registro de PID, PGID, boot ID e scope presentes. Sandbox de sistema operacional e supervisão geral da árvore continuam pendentes. |
| AEC-GOV-003 | READY | Código exige ator local, avaliação, digest e evidência válida antes da publicação; testes de adulteração cobrem a regra. |
| AEC-PUB-004 | PARTIALLY_READY | Publicação histórica documentada; banco e Git de origem citados não estão disponíveis aqui para reconferência. |
| Aprovação humana | PARTIALLY_READY | Gateway Unix valida UID e ação. A implantação atual não demonstra separação de contas entre agente e aprovador. |
| Observabilidade operacional | BLOCKED | Não havia CLI de consulta nem agregação de timeline antes da Fase 8. |

## Estado e lacunas operacionais

- O README e o roadmap registram `security-audit@1.0.3` como publicada em uma
  instalação local anterior. Não há banco operacional nesta checkout para confirmar
  a versão ativa. Esta fase não publica a candidata.
- `security-audit@1.0.2` continua bloqueada pelo Registry e pelo Router.
- As definições versionadas de desenvolvimento adversarial, Arena, TDD,
  auditoria de segurança e decisão adversarial existem no código. A lista de
  versões publicadas depende do banco consultado, ausente neste checkout.
- O único adapter de execução integrado é `CodexCliAdapter`. O benchmark já usa
  `ScriptedAdapter` em ambientes descartáveis.
- Os estados de workflow incluem `CREATED`, `VALIDATED`, `RUNNING`,
  `WAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED` e `INTERRUPTED`.
  Execuções também distinguem `STARTING`, `SUCCEEDED` e `TIMED_OUT`.
- `ExecutionResult.usage` registra números reportados pelo executor. O benchmark
  local registra duração e resultados em bases descartáveis. Não havia resumo
  operacional consolidado nem medida geral de custo.
- Os comandos existentes são demonstrações em módulos `aec.execution.demo`,
  `aec.workflows.demo`, `aec.engineering.demo` e `aec.simulation.agents`.
- O Event Store distribui eventos por `events`, `workflow_events`, mensagens,
  execuções e tabelas de metodologia. Não havia consulta única com correlação
  cronológica e tratamento de registros incompletos.

## Evidência histórica e limites

As ADR-035 a ADR-041 e `docs/methodologies/PHASE_7.md` registram as proteções
AEC-SEC-001, AEC-RES-002, AEC-GOV-003 e AEC-PUB-004. Os arquivos de texto em
`.audit/` guardam relatos de verificação anteriores. Os bancos e repositórios
externos citados para publicação e reteste real não estão presentes neste
checkout; o texto histórico não substitui a consulta do Registry operacional.
Código não confiável continua bloqueado sem sandbox de sistema operacional.
