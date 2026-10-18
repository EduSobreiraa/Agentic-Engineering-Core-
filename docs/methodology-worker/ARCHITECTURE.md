# Análise e evolução metodológica

## Diagnóstico antes da Fase 6

A Fase 4 já persistia `engineering_runs`, `engineering_iterations`, revisões tipadas, resultados de gates, execuções ACP e pedidos de aprovação. O Workflow Engine guardava a definição e o hash de cada execução. O ACP já autorizava `METHODOLOGY_PROPOSAL` para o papel `methodology-worker`. A aprovação humana local da Fase 3 usava uma lista de atores fornecida pelo código confiável.

A Fase 5 permanecia aberta no roadmap. Não havia Documentation Worker, manifesto de documentos obrigatórios nem relatórios metodológicos. A Fase 6 adicionou um renderizador mínimo para RFC e avaliação, mas os demais itens da Fase 5 continuam pendentes. O ciclo adversarial da Fase 4 seleciona `1.0.0` em código e não usa o novo Registry. Isso limita a adoção de `1.1.0` ao Workflow Engine por `create_published()`.

O risco principal era confundir uma proposta ACP com autorização humana. O desenho mantém o broker como transporte e grava a decisão em tabelas próprias, com o hash da proposta e a identidade do aprovador. Outro risco era atribuir `1.1.0` a uma execução com definição diferente. `bind_run()` compara o hash da definição completa.

## Componentes e fluxo

`ObservationCollector` lê os registros tipados do mesmo SQLite usado pelas fases anteriores. `metrics.calculate()` computa os indicadores sem LLM. `MethodologyWorker` identifica findings de tipagem e submete a proposta. O worker recebe apenas o coletor e o repositório de propostas. Pode avisar o orquestrador pelo ACP, mas a mensagem aponta para a proposta persistida e não concede autoridade.

`DocumentationWorker` renderiza a RFC, o relatório de avaliação, a decisão e a publicação em Markdown. A criação usa modo exclusivo, então uma segunda gravação com o mesmo nome falha sem substituir o histórico.

`MethodologyEvaluator` constrói a versão candidata em memória, valida o grafo e executa o benchmark registrado para cada tarefa nas duas definições. A avaliação sem benchmark é `INCONCLUSIVE`. `MethodologyGovernance` exige um ator presente em `local_approvers` e uma avaliação `PASS` ou `PASS_WITH_WARNINGS` antes da aprovação. A publicação repete as verificações dentro de uma transação SQLite.

`MethodologyRegistry` guarda versões publicadas, o hash do conteúdo, a versão ativa e vínculos com execuções. `WorkflowEngine.create_published()` seleciona uma definição publicada e fixa sua versão na nova execução. Versões anteriores continuam acessíveis.

## Limites

- A autenticação por `local_approvers` é local, como na Fase 3. Ela não prova identidade entre máquinas.
- O avaliador executa um benchmark local fornecido pelo chamador e registra os resultados por tarefa. O AEC ainda não executa tarefas equivalentes automaticamente pelo Execution Manager. O operador deve conferir o benchmark e as condições antes de aprovar.
- O `AdversarialLoop` da Fase 4 continua fixo em `1.0.0`. O Registry publica definições do Workflow Engine, mas ainda não seleciona a configuração daquele controlador especializado.
- Uma queda entre `WorkflowEngine.create_published()` e `bind_run()` pode deixar uma execução criada sem vínculo no Registry. A definição da execução permanece fixa no Workflow Engine e o vínculo pode ser recuperado com `bind_run()`.
- O indicador de documentação permanece indisponível até a Fase 5 definir os artefatos obrigatórios.
- Não houve teste com dois processos de publicação nem autenticação remota. O SQLite serializa as transações de publicação locais.
- A análise assistida por Codex não foi ligada nesta etapa. Uma integração futura pode usar o `ExecutionAdapter` existente para sugerir hipóteses, mantendo métricas, avaliação e publicação no código determinístico.

## Plano incremental restante

Concluir o Documentation Worker da Fase 5 e definir o manifesto de documentos. Vincular a configuração do `AdversarialLoop` a versões publicadas antes de permitir que esse controlador selecione `1.1.0`. Acrescentar um executor de benchmarks que grave os pares e seus hashes de critérios sem aceitar números arbitrários do chamador.
