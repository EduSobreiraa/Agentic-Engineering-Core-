# Ciclo adversarial v1

`AdversarialLoop` implementa a metodologia `dev-adversarial@1.0.0`. Cada etapa de agente roda como um workflow acíclico da Fase 3; o controlador especializado persiste o número da iteração e limita as repetições. O ACP transporta atribuições, resultados, findings, pedidos de correção, gates e aprovação. O Execution Manager é o único caminho para executar agentes.

O fluxo é `PREPARED → IMPLEMENTED → REVIEWED → GATED`. Uma revisão aprovada e todos os gates aprovados criam um workflow em `WAITING_APPROVAL`. Findings reproduzidos de severidade média ou superior geram `REWORK_REQUEST` até o limite. Revisão inconclusiva, gate reprovado, resultado externo incerto ou limite esgotado bloqueiam a execução. `APPROVED` registra apenas a decisão humana; nenhum merge ou push é executado.

`run()` pode ser chamado novamente. Workflows e mensagens têm IDs determinísticos. Um commit capturado pelo controller após um workflow concluído pode ser reconstruído se a gravação no banco falhar; um commit externo ou resultado de agente incerto bloqueia a execução. Travas locais coordenam managers, mas a instalação deve usar um único controller adversarial ativo por execução.

O MVP foi verificado com agentes simulados e em [uma execução real com Codex](REAL_CODEX_EXPERIMENT.md), encerrada em `WAITING_APPROVAL`. Essa amostra não cobre aprovação humana ou concorrência. A recuperação de commit após falha de persistência foi verificada com falha injetada e agente simulado. Novas execuções reais exigem autorização explícita para consumir a franquia.
