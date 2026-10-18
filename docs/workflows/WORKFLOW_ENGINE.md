# Workflow Engine

O motor executa grafos acíclicos definidos em YAML. `load()` valida o arquivo; `WorkflowEngine.create()` confere executores e validadores registrados, persiste a definição e cria uma execução `VALIDATED`; `run()` percorre as etapas elegíveis até concluir, falhar, interromper ou aguardar aprovação. A interface pública está em `aec.workflows`.

As etapas `agent` passam pelo `ExecutionManager`: o motor publica `TASK_ASSIGNED` no ACP e o manager executa o adapter, guarda o resultado e publica `ARTIFACT_SUBMITTED` quando há sucesso. Falhas do workflow publicam `TASK_FAILED` para uma identidade `QUALITY_GATE`, com ID determinístico para evitar duplicação. `validation` chama uma função Python registrada que retorna `bool` e publica `VALIDATION_RESULT` no ACP. `human_approval` registra o pedido no SQLite e suspende o fluxo. O texto de um agente não concede autorização nem aprova uma etapa.

O banco compartilhado guarda a definição, seu hash SHA-256, estados, tentativas, aprovações e eventos. Consulte [esquema](WORKFLOW_SCHEMA.md), [estados](STATE_MACHINE.md) e [recuperação](RECOVERY_AND_RETRIES.md). O manager coordena execuções e o motor toma uma trava local por workflow. Não há coordenação distribuída nem interface gráfica.

## Integração

Crie um `Broker`, identidades locais `ORCHESTRATOR`, `QUALITY_GATE` e `IMPLEMENTER`, um `ExecutionManager` com adapters registrados e um `WorkflowEngine` com validadores registrados. O diretório de trabalho deve existir e estar entre as raízes permitidas do manager. `workspaces` pode associar um diretório distinto a cada agente; sem associação, todos usam o diretório padrão. O motor usa pedidos de execução `read-only`; uma definição YAML não habilita escrita no workspace. IDs de execução e mensagens incorporam execução, etapa e tentativa para correlação.

O ACP transporta atribuições e submissões; o motor consulta o resultado estruturado da Execution Layer para decidir o estado. Uma mensagem recebida isoladamente não muda o estado do workflow. O manager impede reutilização insegura de uma atribuição. Publicação ACP e atualização de estado do workflow ocorrem em transações separadas; após uma falha nessa fronteira, use `recover()` e inspecione o histórico antes de agir.
