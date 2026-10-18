# Ciclo adversarial v1

`AdversarialLoop` implementa a metodologia `dev-adversarial@1.0.0`. Cada etapa de agente roda como um workflow acíclico da Fase 3; o controlador especializado persiste o número da iteração e limita as repetições. O ACP transporta atribuições, resultados, findings, pedidos de correção, gates e aprovação. O Execution Manager é o único caminho para executar agentes.

O fluxo é `PREPARED → IMPLEMENTED → REVIEWED → GATED`. Uma revisão aprovada e todos os gates aprovados criam um workflow em `WAITING_APPROVAL`. Findings reproduzidos de severidade média ou superior geram `REWORK_REQUEST` até o limite. Revisão inconclusiva, gate reprovado, resultado externo incerto ou limite esgotado bloqueiam a execução. `APPROVED` registra apenas a decisão humana; nenhum merge ou push é executado.

`run()` pode ser chamado novamente. Workflows e mensagens têm IDs determinísticos. Se o commit do Implementador divergir do último commit persistido, a execução bloqueia em vez de repetir uma operação incerta. Uma instalação deve usar um único manager ativo por banco.

O MVP foi verificado com agentes simulados. A execução com dois Codex reais continua sem validação nesta fase e exige autorização explícita para consumir a franquia.
