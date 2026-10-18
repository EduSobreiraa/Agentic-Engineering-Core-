# Findings e contratos

`ImplementationResult` vincula tarefa, iteração, commit base, commit produzido, arquivos alterados e artefatos. `QAReviewResult` vincula a revisão ao commit exato e contém findings, testes declarados e limitações. `ReviewFinding` exige classificação `REPRODUCED` ou `HYPOTHESIS`, severidade e descrição. Um finding reproduzido exige passos de reprodução ou evidência. Uma revisão `FAILED` exige ao menos um finding reproduzido de severidade média, alta ou crítica; `PASSED` não admite esses impedimentos. Hipóteses altas ou críticas bloqueiam conclusão para esclarecimento.

O manager valida JSON QA antes de publicar. Cada finding gera `FINDING_REPORTED` e o relatório completo fica em artefato versionado por hash. A classificação do QA é informação para a decisão, não substitui gates. `ReworkRequest` é construído pelo orquestrador com IDs dos findings, commit base, critérios originais e iterações restantes.
