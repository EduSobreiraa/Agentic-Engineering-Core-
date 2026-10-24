# Aceite da Fase 8 — revisão final

Revisão em 2026-10-24 da branch `feature/fase-8-integracoes-observabilidade`.
**Classificação de aceite: PARTIAL.** O Marco A (Operational Core) está
COMPLETE na verificação local supervisionada; o Marco B (Integrated Workspace)
está PARTIAL. Este aceite encerra a rodada da Fase 8 sem tratar pendências como
entregues. Nenhum merge ou publicação metodológica foi feito nesta revisão.

## Evidências examinadas

- [Baseline](PHASE_8_BASELINE.md): 208 testes aprovados e 12 pulados antes da
  Fase 8; matriz de prontidão e limites herdados.
- [Marco A](OPS_HARDENING.md): política `ExecutionBudget` v2, concorrência
  entre processos em workspaces distintos, cota persistida e turno Codex real
  com modelo explícito. Os ensaios OPS-001, OPS-003 e OPS-002 foram executados.
- [Marco B](INTEGRATED_WORKSPACE.md): gateway pstack até o pedido recebido pelo
  adapter Codex real, investigação Orca e três pares Codex reais.
- [Relatório bruto dos seis runs](evidence/real_codex_benchmark_20261024.json)
  e [manifesto de digests](evidence/real_codex_benchmark_20261024_manifest.json).
  Os [inputs e gates das três tarefas](evidence/real_codex_benchmark_20261024_tasks.json)
  foram conferidos pelos hashes de prompt do relatório.
  O relatório copiado é byte a byte igual ao original de `/tmp` e tem SHA-256
  `38db439e4a13b52dc9cb45a05bfaf1082ea41090b862b803fddd9114e81e1e83`.
  O manifesto guarda o caminho, tamanho e digest conferido de cada SQLite.
  Os bancos operacionais continuam nos caminhos locais registrados; não foram
  copiados para o repositório porque podem conter identificadores de sessão
  Codex. Seus digests permitem conferir os arquivos enquanto disponíveis.
- [Relatório bruto do gateway](evidence/pstack_gateway_20261024.json):
  resposta `ENABLED`, digest da skill, vínculo, prompt e gate.

## Critérios originais de conclusão

`CUMPRIDO` significa verificado no escopo local indicado. `LIMITADO` significa
capacidade implementada com alcance menor que o critério original. `NÃO
ENTREGUE` registra uma capacidade ainda ausente, sem inferir que seja inviável.

| Critério da Fase 8 | Aceite | Evidência ou limite |
| --- | --- | --- |
| Baseline, prontidão e pendências das Fases 0–7 | CUMPRIDO | Baseline registra estados e ausência do banco histórico de publicação. |
| CLI de consulta para status, runs, agentes, aprovações, processos, métricas e metodologias | CUMPRIDO | `OperationalReader` e CLI somente leitura; testes de integração. |
| Timeline de eventos reais, IDs e evidências | CUMPRIDO | Consulta ao Event Store; não inventa eventos ausentes e marca estados desconhecidos. |
| Findings e quality gates visíveis | CUMPRIDO | `runs inspect` projeta severidade e resultado do gate sem conteúdo sensível. |
| Inspeção de interrupções e quarentenas sem liberá-las | CUMPRIDO | Observação conservadora do processo; incerteza retorna `UNKNOWN`. |
| Saída JSON e tratamento de registros incompletos | CUMPRIDO | CLI e regressões de observabilidade. |
| Política versionada de concorrência, invocações, retries, timeout, duração e iterações | CUMPRIDO | `ExecutionBudget` v2, limites aplicados antes das operações e teste OPS-003 entre processos. |
| Modelo e esforço por agente ou workflow, sem fallback silencioso | LIMITADO | Pedido `--model gpt-6.1-sol` e esforço `medium` passaram com Codex real; modelo efetivo não foi informado pelo CLI. |
| Métricas reportadas, sem estimar consumo ausente | CUMPRIDO | Contagens, duração e tokens reportados; campos indisponíveis ficam `UNKNOWN` ou `NOT_AVAILABLE`. |
| Skill pstack opcional aprovada, fixada por digest e propagada ao executor | CUMPRIDO | INT-001 confirmou `skill.enable` → Registry → ACP → `ExecutionRequest` e gate com Codex real. |
| Consulta Orca opcional e somente leitura | CUMPRIDO | Adapter de contexto de worktree consultou CLI oficial e filtrou campos. |
| Supervisão de runs AEC na interface Orca | NÃO ENTREGUE | CLI 1.4.229 e guia versionado não mostraram contrato documentado para runs externos; [investigação](ORCA_INTEGRATION.md). |
| Benchmark rastreável e pares equivalentes com Codex real | LIMITADO | Seis runs, três pares, gates iguais e relatório bruto; só uma repetição por braço e intervenção de skill na mesma definição experimental. Resultado `INCONCLUSIVE`. |
| Comparação conclusiva entre metodologias publicadas | NÃO ENTREGUE | Não houve amostra nem desenho suficientes para inferir eficácia entre versões metodológicas. |
| E801–E806 e regressão | LIMITADO | E801–E804 e E806 têm evidência indicada; E805 cobre investigação e adapter, não supervisão integrada. Suíte padrão passou, mas 12 testes opt-in/externos foram pulados. |
| Governança supervisionada; sem merge nem publicação automática | CUMPRIDO | Auditoria do diff e regressão: ações continuam autorizadas; esta revisão não mesclou nem publicou. |
| Operação sem Orca e sem pstack | CUMPRIDO | Integrações opcionais; CLI e núcleo não dependem delas. |
| Restrições arquiteturais da fase | CUMPRIDO | Sem novo provedor de LLM, modelo local, infraestrutura distribuída, dashboard web, nova metodologia ou API comercial. |
| Isolamento de código não confiável e aprovação remota segura | NÃO ENTREGUE | Fora do escopo local supervisionado; permanecem riscos para uso mais amplo. |

## Auditoria de governança e regressão

O diff da branch não modifica `src/aec/methodology/governance.py` nem
`src/aec/security/identities.py`. A extensão do gateway para `skill.enable`
exige ação autorizada, deriva o ator do UID pelo socket e rejeita campos
inesperados. O `SkillRegistry` exige aprovação local, estado `VALIDATED`,
permissões e digest íntegro antes de resolver uma skill. O Workflow Engine
confere novamente o vínculo antes do turno. Os caminhos de aprovação de run,
publicação metodológica e reconciliação continuam separados e foram cobertos
pela regressão; não se observou mudança involuntária de governança no diff
revisado. Esta auditoria de código e testes não demonstra separação real de UID.

`uv run ruff check .` passou. `uv run mypy` passou em 91 arquivos.
`uv run pytest` terminou com **232 passed, 12 skipped**. `git diff --check`
passou. Os turnos Codex reais são ensaios opt-in documentados nos Marcos A e
B, fora da suíte padrão. A integridade dos seis bancos ainda presentes em
`/tmp` foi reconferida com o manifesto após a cópia do relatório.

## Pendências transferidas

**Evolução do produto:** reavaliar Orca somente quando houver interface
documentada para runs externos; melhorar o acesso à observabilidade sem
enfraquecer a CLI; comparar versões metodológicas com mais tarefas e
repetições, preservando snapshot completo do código experimental.

**Hardening de segurança:** isolamento de sistema operacional antes de código
não confiável; serviço, agentes e aprovador em identidades e permissões
separadas; ampliar a supervisão de descendentes e desenhar aprovação remota
com identidade, autorização e transporte confiáveis antes de habilitá-la.
Essas frentes permanecem backlog, sem iniciar a fase seguinte.
