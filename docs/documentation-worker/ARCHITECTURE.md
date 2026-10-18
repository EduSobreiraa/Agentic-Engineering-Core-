# Worker de Documentação

## Limite do componente

O worker recebe pedidos pelo ACP e gera Markdown a partir dos registros locais do AEC. Ele lê o SQLite compartilhado e os artefatos referenciados, mas não executa código de projeto, não concede aprovação e não altera a metodologia. O Codex CLI continua sob controle da Execution Layer; a geração determinística não o utiliza.

O fluxo separa coleta, normalização, rastreabilidade, renderização e persistência. Essa divisão mantém o texto final subordinado aos registros tipados. Mensagens de agentes podem descrever uma implementação ou teste, mas não substituem um `GateResult` nem uma linha de `workflow_approvals`.

## Fontes de verdade

| Informação | Origem |
| --- | --- |
| Objetivo, critérios, estado e commits da engenharia | `engineering_runs` |
| Implementação, revisão QA e gates por iteração | `engineering_iterations` |
| Mensagens e referências de artefatos | `messages` e `Broker.get_history()` |
| Estado e definição versionada de workflows | `workflow_runs`, `workflow_steps` e `workflow_events` |
| Decisão humana local | `workflow_approvals` |
| Status e referências das execuções externas | `executions` |

O worker registra lacunas quando uma fonte não contém a informação exigida. Uma referência a arquivo só comprova conteúdo quando o arquivo existe e seu SHA-256 corresponde ao hash registrado. O commit identifica a revisão do código; nomes de arquivos não identificam versões.

## Categorias de documento

Relatórios de execução, resumos de QA e alterações são registros históricos. Triggers do SQLite rejeitam `UPDATE` e `DELETE` em `documentation_records` e `adr_records`. Uma revisão recebe um novo ID ou número e preserva o documento anterior. Documentação técnica evolutiva requer escopo e autorização do workflow antes de qualquer alteração. Documentos de governança, incluindo políticas e metodologias, aceitam propostas para revisão, nunca edição direta pelo worker.

ADRs têm estados `PROPOSED`, `ACCEPTED`, `REJECTED` e `SUPERSEDED`. `adr_records` guarda revisões vinculadas à execução com hash de conteúdo. A passagem para `ACCEPTED` depende de uma decisão humana registrada por um workflow com `metadata.adr_id` correspondente. Uma mensagem de agente não muda esse estado.

## Limites operacionais

O broker fornece entrega pelo menos uma vez. O processamento documental usa IDs estáveis para não gerar um segundo registro histórico após redelivery. O SQLite é a fonte de verdade; o arquivo Markdown é o artefato recuperável. O núcleo presume um único writer ativo por banco e handles válidos no processo local. Não há autenticação humana entre máquinas nem publicação automática de documentação externa.
