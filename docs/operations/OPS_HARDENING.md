# Fase 8, Marco A — Operational Core

Verificação local em 2026-10-24. **Marco A: COMPLETE. Fase 8: PARTIAL.**
O marco cobre OPS-001, OPS-003 e OPS-002 nesta ordem. Os limites abaixo
impedem tratar a Fase 8 inteira como concluída.

## OPS-001 — Política unificada

`ExecutionBudget` versão 2 é o contrato declarativo persistido por run, com
digest SHA-256. Exige todos os campos:

```python
ExecutionBudget(
    version="2",
    max_concurrent_agents=1,
    max_invocations_per_run=2,
    max_retries=0,
    execution_timeout_seconds=60,
    max_duration_seconds=300,
    max_iterations=2,
)
```

O manager exige que `max_concurrent_agents` concorde com seu limite local e
recusa timeout, duração e cota antes de chamar o adapter. Cada recusa gera
`EXECUTION_BLOCKED` com motivo conhecido na timeline. O Workflow Engine valida
paralelismo, duração, timeout e retries de validação antes de criar o run. O
ciclo adversarial valida seu limite de iterações. Retries de agentes continuam
proibidos, e operações externas incertas não são repetidas automaticamente.
Os budgets versão 1 persistidos continuam legíveis com a semântica anterior.
Para runs diretos, a duração começa no primeiro turno registrado pelo AEC;
para workflows, usa o início persistido do workflow. Horários enviados em
mensagens ACP não estendem o prazo.

## OPS-003 — Processos e orçamento

O teste `test_budget_and_concurrency_across_processes_with_distinct_workspaces`
iniciou dois managers em processos Python independentes. Cada execução usou
um diretório próprio (`work-a` e `work-b`); o adapter de teste escreveu apenas
nas tabelas de efeitos do banco. Com uma vaga, o segundo turno aguardou o
primeiro, e o pico observado foi 1. Uma terceira execução em `work-c` foi
bloqueada após duas invocações persistidas: duas linhas em `executions` e duas
em `external_effects`. O teste não usa workspace mutável compartilhado.

## OPS-002 — Codex real

Foi executado um turno real, somente leitura, com autenticação Codex existente:

```bash
uv run python -m scripts.ops_model_probe \
  --workspace /tmp/aec-ops-model-final-20261024 \
  --model gpt-6.1-sol
```

O adapter passou `--model gpt-6.1-sol` e
`--config model_reasoning_effort=medium`. O resultado persistido foi
`SUCCEEDED`; o gate verificou a resposta exata `AEC_OPS_OK`. O relatório está em
`/tmp/aec-ops-model-final-20261024/report.json` e o SQLite no mesmo diretório.
`requested_model` é `gpt-6.1-sol`; `effective_model` permanece `UNKNOWN`, pois o
CLI não informou uma identidade efetiva nos eventos usados pelo adapter. O
uso reportado foi 23.454 tokens de entrada, 13.184 de entrada em cache e 8 de
saída. Esses números são os campos do Codex, não estimativas de custo.

## Regressão e limites

`uv run ruff check .` passou. `uv run mypy` não encontrou erros em 90 arquivos.
`uv run pytest` terminou com **231 passed, 12 skipped**. O ensaio Codex acima
foi opt-in, fora da suíte padrão. A CLI de timeline mostra snapshot da política,
modelo solicitado, esforço, modelo efetivo quando conhecido e resultado do
turno. Nenhum teste de concorrência executou Codex em paralelo.

O Marco B continua pendente: a interface Orca não exibe os runs AEC. Também
permanecem pendentes o caminho completo de aprovação da skill pelo gateway em
um ensaio real, benchmarks Codex mais amplos, separação de UID para aprovação e
sandbox de sistema operacional para código não confiável. A candidata
`security-audit@1.0.3` não foi republicada nem reconfirmada no Registry da
instalação histórica, cujo banco não acompanha este checkout.
