# Máquina de estados

As transições são verificadas antes de cada gravação pelo `WorkflowRepository`. Estados terminais rejeitam novas transições.

| Workflow | Próximos estados permitidos |
| --- | --- |
| `CREATED` | `VALIDATED`, `CANCELLED` |
| `VALIDATED` | `RUNNING`, `CANCELLED` |
| `RUNNING` | `WAITING_APPROVAL`, `COMPLETED`, `FAILED`, `CANCELLED`, `INTERRUPTED` |
| `WAITING_APPROVAL` | `RUNNING`, `FAILED`, `CANCELLED` |
| `INTERRUPTED` | `CANCELLED` |

| Etapa | Próximos estados permitidos |
| --- | --- |
| `PENDING` | `READY`, `SKIPPED`, `CANCELLED` |
| `READY` | `RUNNING`, `WAITING_APPROVAL`, `CANCELLED` |
| `RUNNING` | `SUCCEEDED`, `FAILED`, `INTERRUPTED`, `CANCELLED`, `READY` |
| `WAITING_APPROVAL` | `SUCCEEDED`, `FAILED`, `CANCELLED` |

`COMPLETED`, `FAILED` e `CANCELLED` do workflow, e `SUCCEEDED`, `FAILED`, `SKIPPED`, `CANCELLED` e `INTERRUPTED` das etapas não têm saída. O motor conclui somente quando todas as etapas estão `SUCCEEDED` ou `SKIPPED` e nenhuma aprovação obrigatória foi ignorada. Aprovação ignorada ou falha de etapa encerra o workflow como `FAILED`; resultado externo incerto o deixa `INTERRUPTED`. Cada mudança de estado e decisão de aprovação gera evento no mesmo commit SQLite da mudança.

Uma etapa dependente de falha, cancelamento, interrupção ou etapa ignorada é marcada `SKIPPED`. A etapa de aprovação fica `WAITING_APPROVAL` até `decide()` receber uma decisão local autorizada. Aprovação retorna o workflow a `RUNNING`; chame `run()` para avançar. Rejeição marca a etapa e o workflow `FAILED`.
