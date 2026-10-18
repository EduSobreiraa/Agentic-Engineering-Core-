# Análise e evolução metodológica

## Diagnóstico antes da Fase 6

A Fase 4 já persistia `engineering_runs`, `engineering_iterations`, revisões tipadas, resultados de gates, execuções ACP e pedidos de aprovação. O Workflow Engine guardava a definição e o hash de cada execução. O ACP já autorizava `METHODOLOGY_PROPOSAL` para o papel `methodology-worker`. A aprovação humana local da Fase 3 usava uma lista de atores fornecida pelo código confiável.

A Fase 5 agora fornece `DocumentationWorker`, coleta estruturada, relatórios de execução, ADRs e artefatos imutáveis. `MethodologyVersion.required_documents` declara os tipos exigidos para cálculo de completude documental; versões sem manifesto continuam com essa métrica indisponível. O ciclo adversarial aceita uma versão publicada compatível e executa validadores declarados antes do QA. Sem seleção explícita, mantém o comportamento legado `1.0.0`.

O risco principal era confundir uma proposta ACP com autorização humana. O desenho mantém o broker como transporte e grava a decisão em tabelas próprias, com o hash da proposta e a identidade do aprovador. Outro risco era atribuir `1.1.0` a uma execução com definição diferente. `bind_run()` compara o hash da definição completa.

## Componentes e fluxo

`ObservationCollector` lê os registros tipados do mesmo SQLite usado pelas fases anteriores. `metrics.calculate()` computa os indicadores sem LLM. `MethodologyWorker` identifica findings de tipagem e submete a proposta. O worker recebe apenas o coletor e o repositório de propostas. Pode avisar o orquestrador pelo ACP, mas a mensagem aponta para a proposta persistida e não concede autoridade.

`DocumentationWorker.create_methodology_document()` renderiza a RFC, o relatório de avaliação, a decisão e a publicação a partir dos registros metodológicos persistidos. Os documentos ficam na mesma área de artefatos da Fase 5, com hash e registro imutável no SQLite. Repetir a geração com o mesmo conteúdo devolve o mesmo ID.

`MethodologyEvaluator` constrói a versão candidata em memória, valida o grafo e executa o benchmark registrado para cada tarefa nas duas definições. A avaliação sem benchmark é `INCONCLUSIVE`. `MethodologyGovernance` exige um ator presente em `local_approvers` e uma avaliação `PASS` ou `PASS_WITH_WARNINGS` antes da aprovação. A publicação repete as verificações dentro de uma transação SQLite.

`MethodologyRegistry` guarda versões publicadas, o hash do conteúdo, a versão ativa e vínculos com execuções. `WorkflowEngine.create_published()` seleciona uma definição publicada e fixa sua versão na nova execução. Versões anteriores continuam acessíveis.

## Limites

- A autenticação por `local_approvers` é local, como na Fase 3. Ela não prova identidade entre máquinas.
- O avaliador executa um benchmark local fornecido pelo chamador e registra os resultados por tarefa. O AEC ainda não executa tarefas equivalentes automaticamente pelo Execution Manager. O operador deve conferir o benchmark e as condições antes de aprovar.
- O `AdversarialLoop` aceita somente a topologia conhecida de Implementador, validadores anteriores ao QA, QA e aprovação humana. Outras metodologias publicadas continuam restritas ao Workflow Engine genérico.
- Uma queda entre `WorkflowEngine.create_published()` e `bind_run()` pode deixar uma execução criada sem vínculo no Registry. A definição da execução permanece fixa no Workflow Engine e o vínculo pode ser recuperado com `bind_run()`.
- Versões sem `required_documents` mantêm a completude documental indisponível. O cálculo verifica o hash do registro e a integridade do arquivo.
- Não houve teste com dois processos de publicação nem autenticação remota. O SQLite serializa as transações de publicação locais.
- A análise assistida por Codex não foi ligada nesta etapa. Uma integração futura pode usar o `ExecutionAdapter` existente para sugerir hipóteses, mantendo métricas, avaliação e publicação no código determinístico.

## Plano incremental restante

Configurar `required_documents` nas versões que exigirem relatórios. Acrescentar um executor de benchmarks que grave os pares e seus hashes de critérios sem aceitar números arbitrários do chamador.
