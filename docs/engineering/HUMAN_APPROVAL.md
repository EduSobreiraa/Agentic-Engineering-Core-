# Aprovação humana

Após QA positivo e gates aprovados, o controlador cria um passo `human_approval` do Workflow Engine e publica `APPROVAL_REQUEST`. O histórico reúne tarefa, critérios, commits, iterações, findings, gates e mensagens das subexecuções. `decide(run_id, actor, approved)` usa os aprovadores locais da Fase 3. A rejeição encerra o fluxo como `REJECTED`; aprovação marca `APPROVED` sem merge ou push.

A autorização local por nome de ator não prova identidade entre máquinas. O operador deve inspecionar `report(run_id)`, que reúne especificação, critérios, diff, commits, findings, correções e gates, e pode consultar `history(run_id)` para mensagens e estados. O AEC não transforma aprovação técnica em permissão de integração.
