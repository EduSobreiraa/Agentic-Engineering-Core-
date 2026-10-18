# Experimento real do ciclo adversarial: fila com leases

Em 2026-10-18, o operador executou `dev-adversarial@1.0.0` em um repositório temporário. O `run_id` foi `1ab67706-cd65-42cc-89f2-0e9240a26af2`, com tarefa `leased-priority-queue`. O banco SQLite foi consultado em modo somente leitura em `/tmp/aec-real-queue-01p6b4vz/aec.sqlite` e passou em `PRAGMA integrity_check`. O arquivo final estava em `/tmp/aec-real-queue-01p6b4vz/worktrees/1ab67706-cd65-42cc-89f2-0e9240a26af2-implementer/job_queue.py`, SHA-256 `1e286d64d661cd65fc538c9f0f1a05dc92510379114448e874e86238a6190c3a`. Esses caminhos são temporários e podem desaparecer.

| Etapa | Evidência persistida |
|---|---|
| Primeira implementação | Commit `5d58ed3245733a3c9aee8eb82b134aa64a77c02a`; oito testes e integridade do snapshot passaram. |
| Primeiro QA | Finding `invalid-nan-configuration`, severidade `MEDIUM`, reproduzido: NaN passa pela validação positiva e impede a expiração do lease. |
| Correção | Uma mensagem ACP `REWORK_REQUEST` e segunda implementação no commit `72efe2d3e2765968cfb70597ecba83788cd246d6`. |
| Revisão final | QA `PASSED`; oito testes e integridade do snapshot passaram novamente. |
| Encerramento | `engineering_runs.status=WAITING_APPROVAL`; aprovação do workflow `PENDING`, sem `decided_by`. Quatro execuções Codex tiveram estado `SUCCEEDED`. |

Os quatro registros de execução somam 682.822 tokens de entrada, incluindo 591.744 em cache, e 7.637 de saída. O intervalo entre o início da primeira execução e o fim da quarta é 147,19 segundos. O operador relatou que uma nova chamada manteve `WAITING_APPROVAL` sem repetir execuções; a consulta confirmou quatro execuções persistidas, mas não reproduziu essa segunda chamada. O operador também relatou uma primeira tentativa impedida pelo sandbox antes de iniciar agente; ela não faz parte da execução medida.

Esta é uma amostra. Ela demonstra detecção e correção de um defeito fora dos testes fornecidos, sem demonstrar vantagem de eficiência sobre outra metodologia. Não houve aprovação humana, merge ou alteração no repositório AEC. Concorrência e persistência da fila estavam fora da tarefa. Não foi testada recuperação de commit após falha do banco nem coordenação de múltiplos processos.
