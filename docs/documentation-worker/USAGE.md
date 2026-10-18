# Gerar um relatório de execução

O exemplo abaixo pressupõe uma execução de engenharia já gravada no banco. Use o mesmo `Broker` do orquestrador. O worker não inicia o Codex CLI.

```python
from aec.communication.broker import Broker
from aec.documentation import DocumentationWorker, request_documentation
from aec.security.identities import Role

broker = Broker("aec.sqlite")
orchestrator = broker.register("orchestrator", Role.ORCHESTRATOR)
documentation = broker.register("documentation", Role.DOCUMENTATION)
worker = DocumentationWorker(broker, documentation)

run_id = "<id de execução existente>"
task_id = "<id da tarefa dessa execução>"
request_documentation(broker, orchestrator, documentation, run_id, task_id)
result = worker.process_next()
if result is not None:
    markdown = worker.get_document(result.document_id)
```

`run_id` e `task_id` devem identificar a mesma execução persistida. `process_next()` consome uma entrega ACP e publica o resultado. Um pedido repetido conserva o ID documental; `get_document()` confere o hash antes de devolver o texto. Para criar uma nova revisão histórica depois de novos eventos, passe outro `correlation_id` a `request_documentation()`.

Para acionar a documentação pelo Workflow Engine, registre `workflow_documentation_validator(broker, orchestrator, worker)` no mapa de validadores e configure um passo `validation` com as entradas `engineering_run_id` e `task_id`. O passo retorna sucesso somente após a publicação documental. Configure dependências para executá-lo depois da validação de engenharia e antes da etapa que precisa do relatório. Um relatório também pode registrar uma execução bloqueada, rejeitada ou cancelada.

Use `DocumentationCollector.collect(run_id, task_id)` quando precisar inspecionar os dados normalizados antes da renderização. Os templates Markdown usados em execução ficam em `src/aec/documentation/templates/` e são incluídos no pacote Python.

`record_adr(adr, broker, documentation, run_id, task_id)` guarda uma revisão de ADR no SQLite e a vincula à execução de engenharia. O handle deve ter papel de Documentação ou Orquestrador. `get_adr(broker.db, adr_id, revision)` confere o hash antes de devolvê-la. Uma proposta pode ser gravada como `PROPOSED`. `ACCEPTED`, `REJECTED` e `SUPERSEDED` exigem uma decisão humana registrada em um workflow cuja definição vincula `metadata.adr_id` ao ADR e `metadata.engineering_run_id` à execução. O relatório da execução lista as revisões e seus hashes.

`technical_documentation()` renderiza uma proposta de documentação técnica com fontes. O worker não escreve em repositórios externos nem publica uma decisão. A revisão humana e a atualização do arquivo de destino continuam sob controle do workflow desse projeto.
