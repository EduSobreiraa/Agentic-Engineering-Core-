# Execution Report ${run_id}

Tarefa: ${task_id}

Estado observado: ${status}

Objetivo original: ${specification}

## Critérios de aceite

${criteria}

Workflow: ${methodologies}. Commit base: ${base_commit}. Commit final: ${head_commit}.

Documento: ${document_id}.

Agentes registrados no ACP: ${agents}.

## Execuções registradas

| Execução | Agente | Estado | Código de saída |
| --- | --- | --- | --- |
${executions}

## Histórico de estados do workflow

| Workflow | Passo | Evento | Horário |
| --- | --- | --- | --- |
${state_history}

## Iterações e validação observadas

| Iteração | Estado | Commit | QA declarado | Quality gates registrados |
| --- | --- | --- | --- | --- |
${iterations}

Declarações de testes feitas por agentes não comprovam execução de quality gates.

| Iteração | Gate | Estado | Validador | Evidência objetiva |
| --- | --- | --- | --- | --- |
${gate_rows}

## Findings e correções

${findings}

## Rastreabilidade verificável

| Tarefa | Iteração | Commit | Arquivo alterado | Finding QA | Gate | Aprovação | Documento |
| --- | --- | --- | --- | --- | --- | --- | --- |
${trace}

IDs de requisitos não persistidos: indisponível. O ID do documento consta na referência ACP DOCUMENTATION_GENERATED.

## Artefatos referenciados

${artifacts}

## Aprovação humana registrada

${approvals}

## Decisões arquiteturais registradas

${adrs}

## Lacunas e limitações

${warnings}
