# Auditoria anterior à Fase 5

## Componentes disponíveis

O ACP v0.1 valida envelopes e referências `ArtifactRef`, persiste mensagens antes da entrega e usa handles locais para conferir remetente e rota. `Broker.get_history()` recupera mensagens por execução. A tabela de recibos evita que uma entrega reconhecida seja processada novamente.

O Workflow Engine persiste definições, passos, eventos e aprovações no SQLite. A definição e seu hash ficam fixados por execução. O ciclo adversarial grava a especificação, os critérios, cada `ImplementationResult`, cada `QAReviewResult`, os resultados de `CommandGate` e o estado final em tabelas próprias. `AdversarialLoop.history()` reúne essas tabelas e as mensagens ACP da execução principal e dos workflows internos.

A Execution Layer grava pedidos e `ExecutionResult` no mesmo banco. `CodexCliAdapter` devolve status, texto final, código de saída, horários e uso estruturados. O manager publica referências para saídas permitidas. A Fase 5 pode ler esses registros sem iniciar outro executor ou consultar sessões do Codex.

## Lacunas e limites encontrados

- O ACP ainda não possui pedidos e resultados documentais. O papel `DOCUMENTATION` existe, mas sua rota atual limita-se a `ARTIFACT_SUBMITTED`.
- Passos de agente do Workflow Engine aceitam apenas Implementador e QA. A documentação determinística precisa de uma entrada própria no fluxo, sem simular um agente Codex.
- Aprovação humana local consta em `workflow_approvals`. O pedido ACP de aprovação não prova uma decisão.
- Resultados de testes descritos pelo Implementador ou QA são declarações dos agentes. `GateResult` é o registro dos comandos verificados pelo controlador.
- Há referências a arquivos em mensagens e resultados, mas não existe catálogo documental nem verificação geral de integridade de arquivos históricos. O worker deverá registrar ausência ou divergência sem inventar evidência.
- O núcleo local não autentica humanos entre máquinas e não coordena writers em processos distintos. Esses limites permanecem para documentos e ADRs.

## Ajustes necessários

Adicionar contratos e rotas ACP explícitos para documentação, leitura estruturada das tabelas existentes, persistência de documentos históricos sem sobrescrita e um ponto de acionamento pelo Workflow Engine. A política de aprovação e a separação entre propostas de ADR e decisões aceitas devem permanecer em código determinístico. O núcleo continua independente do Orca e de chamadas pagas.
