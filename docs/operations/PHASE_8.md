# Operação e observabilidade da Fase 8

Esta entrega mantém o SQLite do AEC como fonte de verdade. As consultas não criam
bancos, não executam migrações e não reconciliam processos. O AEC funciona sem
Orca e sem pstack. Consulte o [baseline](PHASE_8_BASELINE.md) para o estado antes
das alterações.

## Consultar a operação

Instale o ambiente com `uv sync --extra dev`. Passe a localização de um banco AEC
existente antes do comando:

```bash
uv run aec --database /caminho/aec.sqlite status
uv run aec --database /caminho/aec.sqlite runs list
uv run aec --database /caminho/aec.sqlite runs inspect RUN_ID
uv run aec --database /caminho/aec.sqlite runs timeline RUN_ID
uv run aec --database /caminho/aec.sqlite agents list
uv run aec --database /caminho/aec.sqlite approvals pending
uv run aec --database /caminho/aec.sqlite processes inspect RUN_ID
uv run aec --database /caminho/aec.sqlite metrics summary
uv run aec --database /caminho/aec.sqlite methodologies list
```

`--json`, antes do nome do comando, produz JSON compacto. Sem a opção, a CLI
imprime JSON indentado. A CLI seleciona apenas IDs, estados, tempos, digests,
referências de artefatos e contagens. Não imprime prompts, conteúdo de mensagens,
descrições de findings, saídas de agentes, IDs de sessão nem tokens de entrega.
O acesso local ao arquivo SQLite continua sujeito às permissões do sistema.

`runs inspect` inclui os passos, execuções, vínculo de metodologia, skills
vinculadas, findings com severidade, resultado booleano dos gates e política de
invocações. `runs timeline` ordena fatos persistidos de `workflow_events`, ACP,
eventos do broker, execuções e aprovações. Um vínculo de metodologia ou skill
aparece como snapshot, com o instante de criação do run; não afirma que houve
um evento de seleção separado. Ausência de registro não gera um evento inferido.
Estados fora dos contratos conhecidos recebem `status_known=false`.

`processes inspect` mostra PID, PGID, boot ID, início registrado, scope,
workspace, estado persistido, presença observada e quarentena. `PRESENT` exige
que PID, PGID, boot ID e início ainda correspondam a `/proc`. A ausência dessa
identidade retorna `UNKNOWN`, pois pode haver descendentes fora do grupo. A
consulta não libera a quarentena. `metrics summary` soma somente tokens presentes
em `ExecutionResult.usage`; duração ausente é `NOT_AVAILABLE` e o modelo efetivo
é `UNKNOWN` quando o Codex não o relata.

## Limites e modelos

`ExecutionManager` aceita `budget=ExecutionBudget(max_invocations_per_run=N)`.
O modelo da política tem `version="1"`; o primeiro turno a fixa com SHA-256 em
`run_execution_budgets`. Managers posteriores usam o mesmo orçamento, mesmo sem
configuração própria, e rejeitam uma política divergente. A admissão usa o lock
local `workspace-admission`, conta as invocações já persistidas e grava
`EXECUTION_BLOCKED` antes de recusar um novo turno. A recusa determinística
falha o passo; não inicia o adapter nem transforma efeitos externos incertos em
retry. A concorrência usa vagas com lock entre processos; `max_concurrency`
versão 1 fica fixado no banco e um manager com valor diferente é recusado.
`WorkflowLimits.max_parallel_steps` limita os passos. O timeout, as tentativas de validação e as
iterações adversariais permanecem nos contratos existentes.

O [Marco A](OPS_HARDENING.md) acrescentou a política versão 2 com tetos
obrigatórios para concorrência, invocações, retries, timeout, duração e
iterações. Os contratos existentes continuam responsáveis por aplicar seus
limites específicos. Recusas de turno entram na timeline, que também apresenta
o snapshot da política e os campos de modelo persistidos.

`ExecutionManager(model_by_agent={"implementer": CodexSettings(model="default",
reasoning_effort="high")})` configura cada agente registrado. Metadados
`codex_model` e `codex_reasoning_effort` da atribuição ACP criada pelo workflow
têm precedência para aquele workflow. `default` omite as opções e usa a
configuração existente do Codex. O adapter passa um modelo explícito com
`--model` e o esforço com `--config model_reasoning_effort=...`; o Codex CLI
0.163.0 instalado aceita essas opções. Valores inválidos são recusados antes do
turno; um modelo indisponível falha sem fallback. O pedido e a configuração
ficam no SQLite em `execution_settings`. O CLI instalado não forneceu uma lista
confiável de modelos disponíveis nem o modelo efetivamente usado no JSONL;
o AEC não o inventa. A configuração global do usuário não é alterada.

## Skills opcionais

`inspect_local_skill()` registra a referência e o SHA-256 de um arquivo
selecionado sob uma raiz confiável. `SkillRegistry` distingue `DISCOVERED`,
`VALIDATED`, `INCOMPATIBLE`, `BLOCKED` e `ENABLED`. A validação confere executor,
permissões, requisitos e digest. O gateway local autentica o UID pelo socket
Unix para `skill.enable` e deriva dele o ator presente em `local_approvers`.
A API direta pressupõe um processo confiável, como as decisões da ADR-041.
`WorkflowEngine.create(..., skills={"passo": ("skill-id",)})` fixa o
vínculo, hash, origem e aprovador no run. Antes do turno, o motor confere o
arquivo novamente e anexa suas instruções à atribuição ACP. A política de
execução, aprovação, sandbox e quality gates permanece em código determinístico.
Uma skill alterada, removida ou não registrada bloqueia o passo antes do Codex.

O catálogo pstack instalado é `0.9.102`, com 65 arquivos `SKILL.md`. A skill
textual `pstack:unslop` não requer script ou permissão de escrita e foi a única
habilitada no experimento. O script
[`scripts/phase8_skill_experiment.py`](../../scripts/phase8_skill_experiment.py)
executa um workflow somente leitura com e sem a skill e aplica o mesmo gate.
Os bancos e o relatório corrigido estão em
`/tmp/aec-phase8-skill-experiment-corrected-20261023`. Os dois runs Codex reais
terminaram `COMPLETED`, passaram no gate e produziram a mesma frase. O SHA-256
da skill foi `0503149a9b85dad02bbc747a3aeb534e2ba07f53c5fd57e491ea3d1e67659aa3`.
Uma tentativa anterior rejeitou uma resposta válida que usou "move" em vez de
"migration"; seu relatório foi preservado em
`/tmp/aec-phase8-skill-experiment-external-20261023`. O gate foi corrigido
antes da segunda comparação. Uma amostra por braço não prova ganho.

O ator `edupires` foi fornecido ao script para este ensaio local solicitado pelo
usuário. O mecanismo de identidade local compartilha a limitação descrita na
ADR-041; o ensaio não demonstra separação de UID para aprovação de produção.
O [Marco B](INTEGRATED_WORKSPACE.md) verificou o caminho completo
`skill.enable` → Registry → atribuição ACP → pedido do adapter Codex real,
incluindo digest e presença do texto da skill no prompt enviado ao adapter.

## Orca local

O Orca 1.4.229 documenta `orca-ide worktree current --json`, consultas de
orquestração e `serve` headless. `OrcaCliAdapter.current_worktree()` chama apenas
o comando local de worktree, verifica host e caminho e devolve quatro campos
permitidos. Um teste com fixture confirma que prévias de terminal não são
repassadas. O adapter consultou o CLI oficial e retornou o worktree desta branch.
Não foi encontrado SDK público para acompanhar runs AEC dentro da interface
Orca. O adapter atual identifica o contexto local, mas não oferece uma tela de
supervisão dos runs no Orca. A CLI `aec` continua o mecanismo de consulta. Não
foram ativados pairing remoto, aprovação móvel ou publicação de artefatos.
A [investigação complementar](ORCA_INTEGRATION.md) examinou as interfaces
documentadas da versão instalada e manteve a supervisão de runs externos como
BLOCKED.

## Benchmark controlado

[`scripts/phase8_benchmark.py`](../../scripts/phase8_benchmark.py) reutiliza
`LocalBenchmarkRunner` e a candidata de gate tipado do teste de metodologia.
O comando `uv run python -m scripts.phase8_benchmark --output-dir DIRETORIO
--repetitions 2` alterna a ordem das versões nas repetições, usa as mesmas duas
tarefas e critérios em cada par e conserva bancos, caminhos, digests, estados,
falhas, duração e invocações no `report.json`. O relatório executado está em
`/tmp/aec-phase8-benchmark-external-20261023/report.json`.

Oito runs roteirizados terminaram sem erro. A base aceitou 2 de 4 casos e usou
8 invocações; a candidata aceitou 4 de 4 e usou 6. A classificação é
`INCONCLUSIVE`: agentes roteirizados e duas tarefas não medem qualidade ou
produtividade de Codex real. O benchmark não publica a candidata; o
`MethodologyEvaluator` existente pode consumir os `BenchmarkRun` estruturados
para propostas supervisionadas.
O Marco B acrescentou [três pares com Codex real](INTEGRATED_WORKSPACE.md),
comparando a mesma definição experimental com e sem uma skill. Os seis gates
passaram; a eficácia metodológica continua INCONCLUSIVE.

## Estado dos cenários e pendências

| Módulo | Classificação | Evidência principal |
| --- | --- | --- |
| CLI, timeline e processos | VALIDATED_WITH_REAL_EXECUTOR | Banco de run Codex real consultado; regressões de registros incompletos. |
| Limites e modelos Codex | VALIDATED_WITH_REAL_EXECUTOR | Política e concorrência verificadas com fixtures entre processos; um turno Codex real usou modelo explícito. |
| pstack | VALIDATED_WITH_REAL_EXECUTOR | `pstack:unslop` em workflow Codex supervisionado. |
| Orca | IMPLEMENTED | Consulta ao CLI real; supervisão dos runs AEC no desktop bloqueada por ausência de interface pública adequada. |
| Benchmark | VALIDATED_WITH_REAL_EXECUTOR | Oito runs roteirizados e seis runs Codex reais pareados; resultado inconclusivo. |

| Cenário | Resultado | Limite |
| --- | --- | --- |
| E801 | VALIDATED_WITH_REAL_EXECUTOR | A CLI reconstruiu a timeline do run Codex `with-skill`. |
| E802 | TESTED_WITH_FIXTURES | Dois processos em workspaces separados compartilharam uma vaga; a terceira invocação foi bloqueada. |
| E803 | VALIDATED_WITH_REAL_EXECUTOR | `--model gpt-6.1-sol` teve turno Codex bem-sucedido; modelo efetivo não foi reportado. |
| E804 | VALIDATED_WITH_REAL_EXECUTOR | `pstack:unslop` foi vinculada e usada em run Codex somente leitura. |
| E805 | IMPLEMENTED | O adapter consultou o Orca real e filtrou a resposta; não há supervisão AEC no desktop. |
| E806 | VALIDATED_WITH_REAL_EXECUTOR | Três pares Codex reais passaram nos gates; comparação de qualidade inconclusiva. |

O banco operacional de publicação citado na Fase 7 não está neste checkout.
Os documentos históricos registram `security-audit@1.0.3` como publicada
localmente, mas esta fase não confirmou o estado ativo dessa instalação nem
republicou a versão. `security-audit@1.0.2` continua bloqueada no código.
AEC-SEC-001, AEC-RES-002 e AEC-GOV-003 passaram na regressão; a evidência real
anterior permanece sujeita aos limites do baseline. Sandbox de sistema
operacional, separação de UID para aprovação, identidade remota e supervisão
móvel continuam pendentes.

## Verificação final

Após o Marco A, `uv run ruff check .` passou, `uv run mypy` não
encontrou problemas em 90 arquivos e `uv run pytest` terminou com **231 passed,
12 skipped**. `git diff --check` passou. Os comandos `uv` foram executados fora
do sandbox do agente porque o cache local do `uv` estava somente leitura nele.
Os 12 testes pulados dependem de execução real opt-in ou pré-condições externas.
O experimento de skill e a consulta ao Orca ocorreram antes do Marco A. O teste
de modelo explícito foi executado depois da política versão 2, com a CLI real.
Os resultados do Marco B e sua regressão estão em
[INTEGRATED_WORKSPACE.md](INTEGRATED_WORKSPACE.md); os números acima são do
fechamento anterior do Marco A.
