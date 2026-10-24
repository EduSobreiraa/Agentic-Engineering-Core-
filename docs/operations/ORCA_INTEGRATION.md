# AEC-INT-002 — Interface Orca para runs externos

Investigação somente leitura em 2026-10-24, com a CLI Orca 1.4.229 e seu guia
versionado (`orca-ide skills get orca-cli`). A CLI AEC permanece a interface
operacional dos runs.

## Interfaces documentadas

- `worktree current --json` informa o contexto local. O adapter AEC existente
  filtra a resposta para ID, caminho, branch e estado do worktree.
- `orchestration run-create` cria um namespace e uma caixa de entrada **do
  Orca**. A ajuda informa que essa operação não agenda workers. `run-list` e
  `run-show` consultam esses runs do Orca; seus contratos não oferecem registro
  de run externo, importação de eventos ou vínculo com um SQLite AEC.
- `agent launch` inicia um agente em um workspace Orca. `terminal list/show/read`
  acompanham terminais geridos pelo Orca. `agent hooks status` consulta hooks
  de status dos agentes geridos pelo Orca. Essas interfaces não aceitam um
  processo Codex já iniciado pelo Execution Manager do AEC como terminal ou
  agente supervisionado.
- `worktree set --comment` permite escrever um comentário no worktree. É
  metadado de worktree, sem contrato de timeline, agentes ou aprovações por run.
  Não foi usado como substituto da supervisão de AEC.

## Resultado

Não encontrei na interface documentada desta versão um mecanismo estável para
acompanhar diretamente, na interface Orca, runs iniciados pelo AEC. Nenhum
estado interno do Orca foi lido ou alterado para simular essa integração. Não
foi criado outro adapter. O `OrcaCliAdapter` local continua somente leitura.

**Classificação:** BLOCKED para supervisão de runs AEC dentro do Orca;
IMPLEMENTED para consulta de contexto do worktree. Se uma versão futura expuser
registro ou assinatura de eventos externos com contrato público, ela poderá ser
avaliada como integração opcional. A CLI `aec` permanece funcional sem Orca.
