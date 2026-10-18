# Tipos de mensagens ACP v0.1

Cada payload rejeita campos extras e exige strings não vazias. O broker valida também o papel do emissor e o papel do destinatário. `ArtifactSubmitted` exige uma referência em `artifact_refs` com o mesmo `artifact_id`.

| Tipo | Payload obrigatório | Emissor | Destinatário |
| --- | --- | --- | --- |
| `TASK_ASSIGNED` | `title`, `instructions` | orchestrator | implementer |
| `ARTIFACT_SUBMITTED` | `summary`, `artifact_id` | implementer, documentation-worker | orchestrator |
| `REVIEW_REQUEST` | `artifact_id`, `criteria` | orchestrator | qa-adversarial |
| `FINDING_REPORTED` | `artifact_id`, `severity`, `description` | qa-adversarial | orchestrator |
| `REWORK_REQUEST` | `finding_message_id`, `instructions` | orchestrator | implementer |
| `VALIDATION_RESULT` | `artifact_id`, `passed`, `evidence` | qa-adversarial, quality-gate | orchestrator |
| `METHODOLOGY_PROPOSAL` | `title`, `rationale` | methodology-worker | orchestrator |
| `APPROVAL_REQUEST` | `subject`, `reason` | orchestrator | orchestrator |
| `TASK_COMPLETED` | `summary`, `validation_message_id` | orchestrator | quality-gate |
| `TASK_FAILED` | `reason` | orchestrator | quality-gate |
| `DOCUMENTATION_REQUEST` | `run_id`, `task_id`, `documentation_type`, `artifact_refs`, `target_scope`, `requested_by`, `correlation_id` | orchestrator | documentation-worker |
| `DOCUMENTATION_GENERATED` | `document_id`, `document_type`, `generated_artifact_ref`, `source_refs`, `validation_status`, `warnings`, `timestamp` | documentation-worker | orchestrator |
| `DOCUMENTATION_FAILED` | `request_message_id`, `reason`, `validation_status=INVALID` | documentation-worker | orchestrator |

`severity` aceita `low`, `medium`, `high` ou `critical`. `passed` é booleano. `TASK_COMPLETED` registra uma declaração autorizada do orquestrador; o broker não verifica se a validação citada foi positiva. Uma fase posterior poderá impor essa regra no control plane. `APPROVAL_REQUEST` é apenas uma solicitação registrada. Nenhum payload concede aprovação ou publica metodologia.

Os payloads documentais preservam o protocolo `0.1` e obedecem à mesma validação de envelope e rota por papel. `DOCUMENTATION_GENERATED` referencia o artefato Markdown gerado. Veja [os contratos e limites do worker](../documentation-worker/CONTRACTS.md).
