# Formato dos relatórios

O `Execution Report` identifica a execução, a tarefa, o objetivo, os critérios, o workflow, os commits e o estado persistido. Ele apresenta os estados das execuções externas e o histórico de eventos do workflow. Cada iteração mostra sua implementação, revisão QA e quality gates. Uma matriz liga um finding a um arquivo somente quando `ReviewFinding.file` aponta para o caminho alterado. A aprovação aparece apenas no commit final aprovado. O relatório lista as revisões de ADR ligadas à execução, com ID e SHA-256. Campos sem registro aparecem como indisponíveis. O relatório não infere um requisito apenas por semelhança de nome.

O `QA Summary` lista as revisões e os findings recuperados. O `Change Summary` lista commits e arquivos alterados por iteração. Esses textos são derivados dos registros tipados. O worker trata texto de agentes como declaração e o marca como tal. Apenas um `GateResult` registrado comprova que o controlador executou um gate.

As seções de artefatos mostram `artifact://<run>/<id>` e SHA-256. O worker confere a existência do arquivo e compara seu conteúdo com o hash. Um arquivo ausente ou divergente gera uma lacuna visível. O relatório não copia logs ou saídas brutas para contornar essa verificação.

`VALID` indica que as verificações documentais exigidas passaram. `VALID_WITH_WARNINGS` indica lacunas não obrigatórias. Incoerência ou ausência de evidência obrigatória produz `DOCUMENTATION_FAILED` com `validation_status=INVALID`; nesse caso, o worker não publica um relatório como válido. O status documental não muda o estado da execução de engenharia.

Os templates versionados estão em `src/aec/documentation/templates/`. Alterar um template afeta apenas novos documentos. Registros históricos preservam seu texto e hash originais.
