# Contratos documentais

O ACP continua na versão `0.1`. Os novos payloads usam o envelope `Message` existente e rejeitam campos extras. O broker confere o handle local do emissor e o papel do destinatário; `requested_by` sozinho não concede permissão.

| Mensagem | Emissor | Destinatário | Payload |
| --- | --- | --- | --- |
| `DOCUMENTATION_REQUEST` | orchestrator | documentation-worker | `DocumentationRequest` |
| `DOCUMENTATION_GENERATED` | documentation-worker | orchestrator | `DocumentationResult` |
| `DOCUMENTATION_FAILED` | documentation-worker | orchestrator | `DocumentationFailure` |

`DocumentationRequest` contém `run_id`, `task_id`, `documentation_type`, `artifact_refs`, `target_scope`, `requested_by` e `correlation_id`. O worker compara os IDs e o solicitante com o envelope autenticado. A geração automática aceita apenas o escopo `aec-run` e os tipos `execution-report`, `qa-summary` e `change-summary`.

`DocumentationResult` contém `document_id`, `document_type`, `generated_artifact_ref`, `source_refs`, `validation_status`, `warnings` e `timestamp`. As referências usam `ArtifactRef` e SHA-256. `DOCUMENTATION_FAILED` identifica o pedido, a causa e `validation_status=INVALID`; uma falha documental não altera o estado da engenharia.

`adr` e `technical-documentation` são tipos reservados no contrato de requisição para autoria supervisionada. A API determinística pode renderizar propostas, mas o worker de execução não escreve esses documentos em projetos externos. Uma solicitação automática desses tipos falha de forma explícita.

O ID do pedido é estável para a combinação de execução, tarefa, tipo e correlação. O documento histórico usa um ID derivado desse pedido. Uma entrega repetida recupera o mesmo registro e confere o hash do arquivo, em vez de sobrescrevê-lo.
