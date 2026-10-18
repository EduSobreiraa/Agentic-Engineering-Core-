# Agendamento

O validador produz uma ordenação topológica e rejeita ciclos. A cada rodada, o motor percorre as etapas em ordem topológica determinística. Uma etapa pendente fica elegível quando todas as suas dependências tiveram sucesso e sua condição, quando configurada, é verdadeira. Dependências falhas ou ignoradas produzem `SKIPPED`.

Etapas elegíveis são executadas em lotes de até `max_parallel_steps`, com `asyncio.gather`. O `ExecutionManager` ainda aplica seu próprio semáforo e uma trava por diretório no processo. Assim, duas etapas de agente no mesmo workspace não executam simultaneamente pelo mesmo manager, mesmo com limite maior no workflow. O parâmetro `workspaces` permite diretórios distintos por agente para concorrência real. Instâncias do motor evitam duas chamadas `run()` simultâneas para o mesmo ID dentro do processo.

Não existe coordenação entre processos ou reserva persistente de slots. Execute um único motor e manager ativos por banco. O grafo é acíclico; ciclos de correção e limites de iteração ficam para a Fase 4.
