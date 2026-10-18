# Métricas de engenharia

`calculate()` filtra por metodologia, versão e início da execução na janela `[start, end)`. Uma janela vazia ou um denominador ausente produz `value=None`. A fonte e a limitação acompanham cada valor.

| Indicador | Numerador | Denominador | Unidade | Fonte e limite |
| --- | --- | --- | --- | --- |
| `first_pass_approval_rate` | Execuções aprovadas cuja primeira revisão foi `PASSED` | Execuções aprovadas com revisão registrada | fração | `engineering_iterations` e aprovação; revisões incompletas não entram |
| `average_review_iterations` | Soma das revisões registradas | Execuções com revisão | revisões por tarefa | `engineering_iterations`; exclui execução sem revisão |
| `rework_rate` | Execuções com revisão `FAILED` | Execuções com revisão | fração | Usa o resultado tipado de QA; não mede o esforço da correção |
| `validation_success_rate` | Execuções com todos os gates registrados aprovados | Execuções com gates registrados | fração | `engineering_iterations.gates` |
| `workflow_completion_rate` | Execuções em `APPROVED` | Execuções iniciadas | fração | `engineering_runs`; aprovação é o fim bem-sucedido |
| `human_intervention_rate` | Decisões humanas registradas | Execuções iniciadas | decisões por tarefa | `workflow_approvals`; apenas decisões locais |
| `time_to_approval` | Soma dos segundos entre início e aprovação | Execuções aprovadas | segundos por tarefa | `engineering_runs.created_at` e `workflow_approvals.decided_at` |
| `documentation_completeness` | Indisponível | Indisponível | fração | Não há manifesto de documentos obrigatórios por metodologia |
| `agent_invocation_count` | Execuções de agentes persistidas | Execuções iniciadas | invocações por tarefa | `executions` |
| `defect_recurrence_rate` | Findings cuja categoria já apareceu na janela | Findings classificados | fração | Revisões tipadas; a classificação pode variar entre projetos |

As métricas não estimam tokens nem custos. A recorrência de categoria descreve frequência, não causa. A média histórica não prova que uma mudança metodológica produziu o resultado.
