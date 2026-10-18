# Contratos metodológicos

Os modelos Pydantic em `src/aec/methodology/contracts.py` usam `strict=True`, rejeitam campos extras e impedem atribuição aos próprios campos. O SQLite preserva o conteúdo publicado e confere seu hash na leitura. `MethodologyVersion` contém a definição validada do Workflow Engine e o conjunto de gates obrigatórios. O único operador declarativo aceito nesta fase é `add_quality_gate` para `type_check` antes de QA. A mudança preserva todos os passos e gates existentes.

`MethodologyObservation` guarda o ID da execução, a versão, o estado, as revisões, os findings, os IDs de mensagens ACP, as referências de artefatos, os gates, as intervenções e os horários conhecidos. `MethodologyMetric` distingue `value=None` de zero e inclui fonte, janela, unidade e limite. `MethodologyFinding` separa observação de hipótese. `MethodologyProposal` exige evidências, versão-base, versão proposta, riscos, validação e reversão.

`BenchmarkCase` identifica a tarefa, o hash dos critérios e as condições. O avaliador executa o benchmark para a definição base e a candidata e produz um `BenchmarkPair` com os resultados. `MethodologyEvaluation` fixa os pares, o hash da proposta, o veredito, as verificações e os limites. `MethodologyApproval` e `MethodologyPublication` registram identidade, data e hash.

O ciclo persistido aceita `DRAFT`, `SUBMITTED`, `UNDER_EVALUATION`, `EVALUATION_FAILED`, `AWAITING_APPROVAL`, `REJECTED`, `CHANGES_REQUESTED`, `APPROVED` e `PUBLISHED`. `SUPERSEDED` está reservado no contrato e ainda não possui transição pública. Uma proposta submetida não pode mudar de conteúdo. Uma revisão exige um novo ID de proposta.

O ACP usa o tipo existente `METHODOLOGY_PROPOSAL`. A mensagem leva o ID e o hash para correlação; o objeto completo permanece no SQLite. O broker valida o papel do remetente. Ele não aceita aprovações metodológicas.
