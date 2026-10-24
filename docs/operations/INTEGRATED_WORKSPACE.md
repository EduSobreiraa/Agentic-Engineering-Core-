# Fase 8, Marco B — Integrated Workspace

Verificação local em 2026-10-24. **Marco B: PARTIAL. Fase 8: PARTIAL.** O
gateway pstack passou de habilitação local a um turno Codex real. A investigação
da CLI Orca não encontrou contrato para supervisionar runs AEC externos. O
benchmark real validou pares reproduzíveis, sem evidência de superioridade.

## AEC-INT-001 — gateway pstack E2E

O script [`phase8_skill_experiment.py`](../../scripts/phase8_skill_experiment.py)
inspecionou `pstack:unslop` da instalação 0.9.102, validou compatibilidade
`codex` e permissão `read-only`, e enviou `skill.enable` ao
`LocalApprovalGateway` por socket Unix. O gateway derivou o UID local via
`SO_PEERCRED`, habilitou a skill no `SkillRegistry` e o `WorkflowEngine` fixou o
vínculo no run. A atribuição ACP e o `ExecutionRequest` recebido pelo adapter
tinham o mesmo SHA-256 de prompt; a instrumentação confirmou que o texto
integral da skill constava desse pedido antes de chamar o Codex CLI real.

```bash
uv run python -m scripts.phase8_skill_experiment \
  --output-dir /tmp/aec-int-001-20261024 \
  --skill-file /home/edupires/.codex/plugins/cache/pstack-claude/pstack/0.9.102/skills/unslop/SKILL.md \
  --approver edupires --single-skill
```

Evidência: `/tmp/aec-int-001-20261024/report.json` e banco
`with-skill/aec.sqlite` no mesmo diretório. O gateway respondeu `ENABLED`; o
digest fixado foi
`0503149a9b85dad02bbc747a3aeb534e2ba07f53c5fd57e491ea3d1e67659aa3`;
`acp_prompt_matches_adapter` e `skill_text_in_adapter_prompt` foram `true`.
O workflow terminou `COMPLETED` e o gate passou. A instrumentação demonstra
propagação ao pedido do adapter, não uma medição do contexto interno do modelo
nem ganho de qualidade. O operador e o serviço usaram o mesmo UID; a separação
de privilégios da ADR-041 segue pendente. **Classificação:
VALIDATED_WITH_REAL_EXECUTOR**, no cenário local somente leitura.

## AEC-INT-002 — supervisão pelo Orca

A [investigação de interfaces](ORCA_INTEGRATION.md) examinou a CLI oficial
1.4.229 e seu guia versionado. `worktree current --json` oferece contexto local
somente leitura, já exposto pelo `OrcaCliAdapter`. As interfaces documentadas de
orquestração e terminais acompanham recursos geridos pelo próprio Orca; não foi
encontrado contrato de importação ou assinatura de runs iniciados pelo AEC.
Nenhum endpoint privado ou estado interno foi usado. **Classificação: BLOCKED**
para supervisão dos runs na interface Orca; consulta de worktree permanece
IMPLEMENTED. A CLI `aec` é a interface operacional oficial.

## AEC-BENCH-001 — três pares com Codex real

[`real_codex_benchmark.py`](../../scripts/real_codex_benchmark.py) executou
três tarefas pequenas de reescrita, cada uma com e sem `pstack:unslop`. Os
braços usaram a mesma definição de workflow, prompt, gate, modelo solicitado
`gpt-6.1-sol`, esforço `medium`, limites e workspace somente leitura isolado.
A ordem dos braços alternou por tarefa. `MethodologyEvaluator._controlled_pairs`
validou o pareamento de critérios e condições. O relatório conserva versão e
digest da definição, digest do gate, prompt e saída, caminho e digest do banco,
commit base, duração, invocações, modelo solicitado, esforço e uso reportado.

```bash
uv run python -m scripts.real_codex_benchmark \
  --output-dir /tmp/aec-bench-real-20261024 \
  --skill-file /home/edupires/.codex/plugins/cache/pstack-claude/pstack/0.9.102/skills/unslop/SKILL.md \
  --model gpt-6.1-sol --approver edupires
```

Evidência: `/tmp/aec-bench-real-20261024/report.json` e seis bancos SQLite
nos diretórios de cada braço. Todos os seis workflows terminaram `COMPLETED`,
os seis gates passaram, nenhum run registrou falha e cada braço consumiu três
invocações. A verificação local conferiu os seis digests de banco. O modelo
efetivo não foi informado pelo Codex CLI. O resultado é **INCONCLUSIVE**:
houve só uma execução por braço e tarefa, e o gate textual é estreito. Este
ensaio compara uma intervenção de skill na mesma definição experimental
`phase8-skill-experiment@1.0.0`; não compara duas metodologias publicadas.
O relatório aponta o commit base, mas o código da branch tinha alterações não
commitadas, então o commit sozinho não reproduz exatamente este checkout.
**Classificação da infraestrutura: VALIDATED_WITH_REAL_EXECUTOR**;
inferência de qualidade entre metodologias: pendente.
O [relatório bruto](evidence/real_codex_benchmark_20261024.json), os
[inputs e gates](evidence/real_codex_benchmark_20261024_tasks.json) e o
[manifesto dos seis digests](evidence/real_codex_benchmark_20261024_manifest.json)
foram preservados no repositório. Os bancos operacionais permanecem nos
caminhos locais do manifesto, sem copiar identificadores de sessão Codex.

## Encerramento e limites

Marco A segue COMPLETE no escopo local supervisionado. Marco B permanece
PARTIAL pela ausência de supervisão de runs AEC na interface Orca e pela
amostra limitada do benchmark. A Fase 8 permanece PARTIAL. Os ensaios não
autorizaram publicação, merge, aprovação remota nem execução de código não
confiável. Sandbox de sistema operacional e separação de privilégios ainda são
pendências de segurança.

## Regressão e auditoria das evidências

`uv run ruff check .` passou; `uv run mypy` passou em 91 arquivos;
`uv run pytest` terminou com **232 passed, 12 skipped**; `git diff --check`
passou. Os 12 testes pulados dependem de execução real opt-in ou de condições
externas. Os ensaios Codex descritos acima foram executados separadamente, e
seus relatórios foram conferidos depois da suíte: o arquivo da skill ainda
corresponde ao digest, o banco do INT-001 está presente, os seis digests dos
bancos do benchmark conferem com os arquivos e os seis runs registram zero
falhas. Os comandos `uv` usaram o cache local fora do sandbox do agente.
