# Avaliação de propostas

O avaliador recebe o ID de uma proposta submetida. Ele recupera o conteúdo persistido, confirma o hash, monta a candidata com operações declarativas e valida o grafo. O Registry confere os validadores registrados antes da publicação.

Sem benchmark e casos, o resultado é `INCONCLUSIVE`. Cada `BenchmarkCase` identifica uma tarefa, o hash dos critérios e as condições. O avaliador chama o benchmark para as duas definições e grava os valores em `BenchmarkPair`. Ele compara as médias na direção declarada pela proposta. Melhoria produz `PASS`; ausência de melhoria ou exceção durante a execução produz `FAIL`. O relatório sempre informa que uma comparação de fixtures não estabelece causalidade.

O cenário de teste usa duas tarefas controladas. As revisões médias da base são 3 e 2; as da candidata são 2 e 1. O avaliador registra 2,5 e 1,5, com `PASS`. Esses valores pertencem a fixtures do teste, não a medições de produção. O avaliador não inventa dados quando não recebe pares.

Um resultado `INCONCLUSIVE` entra em `AWAITING_APPROVAL` para inspeção, mas `decide(..., "APPROVED")` recusa a aprovação. A política inicial não implementa exceção humana para publicar sem avaliação positiva.
