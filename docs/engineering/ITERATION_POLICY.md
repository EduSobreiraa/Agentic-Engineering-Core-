# Política de iteração

Os limites de iterações e duração são positivos e fixados na execução junto com a configuração dos gates. Cada iteração possui linha SQLite com implementação, revisão e gates serializados. IDs de workflow derivados da execução e da iteração evitam repetir uma etapa concluída. A retomada de resultado externo desconhecido bloqueia a execução; não repete automaticamente o agente.

Uma falha QA reproduzida pede correção quando ainda há iterações. Revisão inconclusiva e falha determinística sem correção respaldada por finding bloqueiam. O estado `BLOCKED` preserva worktrees e histórico para inspeção humana. `cancel(run_id, actor)` exige um aprovador local, fecha aprovação pendente e solicita cancelamento ao adapter quando há execução ativa. A reconciliação automática de commits criados imediatamente antes de uma falha do banco ainda não existe.
