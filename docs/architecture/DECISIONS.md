# Decisões arquiteturais

Estas decisões foram aceitas no escopo inicial. Mudanças futuras devem acrescentar um registro com data, motivo, efeitos e vínculo com a execução ou RFC pertinente. Detalhes marcados como pendentes não são contratos implementados.

## ADR-001: Núcleo independente do ambiente de supervisão

**Estado:** aceito em 2026-10-17.

**Contexto:** o Orca ajuda no desenvolvimento e na supervisão, mas sua integração programática ainda não foi verificada.

**Decisão:** o AEC funcionará como pacote Python independente do Orca. Integração com Orca será investigada na Fase 8. Skills do pstack podem inspirar metodologias, sem dependência obrigatória.

**Efeito:** nenhuma interface central presume sessões, APIs ou estado do Orca.

## ADR-002: Executor inicial Codex CLI sem API paga

**Estado:** aceito em 2026-10-17.

**Contexto:** há autenticação do Codex CLI pela assinatura existente e não há orçamento para APIs comerciais adicionais.

**Decisão:** a Fase 2 usará Codex CLI como primeiro executor, após verificar autenticação e modo não interativo. O núcleo não exigirá `OPENAI_API_KEY`. Uma interface de executor permitirá outros CLIs e Ollama futuramente.

**Efeito:** capacidades de sessão, limites e saída estruturada precisarão de validação antes do adapter. Nenhuma credencial existente será modificada pela preparação do repositório.

## ADR-003: ACP independente de executor e metodologia

**Estado:** aceito em 2026-10-17.

**Contexto:** comunicação, auditoria e recuperação devem funcionar com papéis e executores diferentes.

**Decisão:** mensagens terão contratos versionados, identidade, correlação e referências a evidências. O ACP não incorporará comandos do Codex nem semântica de um workflow específico.

**Efeito:** a Fase 1 decidirá envelope, payloads, ACK, autorização e garantias de entrega em uma especificação ACP v0.1.

## ADR-004: SQLite como fonte inicial de verdade

**Estado:** aceito em 2026-10-17.

**Contexto:** o primeiro uso é local e precisa de recuperação após falhas sem serviço adicional.

**Decisão:** SQLite guardará tarefas, mensagens, estados e aprovações. Diretórios guardarão artefatos; Git versionará código e metodologias. JSONL será opcional para exportação de eventos.

**Efeito:** a Fase 1 deverá definir transações, deduplicação e recuperação. Exportações JSONL não poderão divergir da fonte de verdade sem detecção.

## ADR-005: Metodologias versionadas com aprovação humana

**Estado:** aceito em 2026-10-17.

**Contexto:** melhoria contínua não pode permitir que um worker altere suas próprias regras enquanto é avaliado.

**Decisão:** cada execução fixa uma versão publicada de metodologia. O Worker de Metodologia pode propor RFCs, mas uma pessoa aprova toda publicação. Versões anteriores e vínculos com execuções permanecem acessíveis.

**Efeito:** publicação e aprovação serão ações governadas. Propostas não alteram critérios nem execuções em andamento.

## ADR-006: Verificação e autorização determinísticas

**Estado:** aceito em 2026-10-17.

**Contexto:** agentes podem errar ao descrever testes, permissões e resultados.

**Decisão:** políticas, limites, transições críticas e quality gates objetivos serão verificados por código. Ações destrutivas, merge, publicação e mudanças metodológicas exigirão a aprovação humana prevista pela política.

**Efeito:** relatórios dos agentes são insumos de avaliação, não prova final de autorização ou sucesso.

## ADR-007: ACP v0.1 local com SQLite e handles internos

**Estado:** aceito em 2026-10-17.

**Contexto:** a Fase 1 precisa persistir mensagens antes da entrega e recuperar falhas sem serviço externo. A identidade textual no envelope não prova quem enviou a mensagem.

**Decisão:** o broker emite handles locais, confere remetente e rota por papel e grava mensagens, entregas, recibos e eventos em SQLite. `BEGIN IMMEDIATE` serializa reivindicações. Cada claim recebe um token distinto; ACK exige o token vigente. `message_id` é único e republicações conflitantes são rejeitadas. A versão do esquema usa `PRAGMA user_version`.

**Efeito:** a entrega é *at-least-once*. Recibos únicos evitam duplicação no estado do broker, mas efeitos externos exigirão reconciliação. Handles não protegem contra processos locais maliciosos. Leitura de histórico é reservada ao código local confiável. O ACP não decide transições de workflow.

## ADR-008: Execução externa vinculada a uma atribuição ACP

**Estado:** aceito em 2026-10-17.

**Contexto:** um ACK do ACP não garante que um processo externo executou uma única vez. O Codex pode modificar arquivos antes de uma interrupção.

**Decisão:** o manager exige identidade local válida e uma atribuição ACP persistida, vincula cada mensagem de origem a no máximo uma execução, grava estados e nunca reinicia automaticamente turnos interrompidos. `workspace-write` exige concessão ao agente no manager e `metadata.workspace_write=true` na atribuição do orquestrador. Somente o manager publica resultados com identidade autorizada. Sessões internas AEC e IDs do Codex ficam separados. O isolamento de escrita por diretório vale dentro de um único manager.

**Efeito:** o SQLite conserva resultado e histórico para inspeção. A implantação deve usar um manager ativo por banco; coordenação entre processos requer projeto posterior. Código gerado não aprova seu próprio quality gate.

## ADR-009: Workflows acíclicos e definição fixada por execução

**Estado:** aceito em 2026-10-18.

**Contexto:** execução reproduzível exige dependências sem ambiguidade e proteção contra mudanças posteriores no YAML.

**Decisão:** a Fase 3 aceita um grafo acíclico. O loader usa PyYAML `safe_load` para interpretar YAML e Pydantic para validar referências, ciclos, versão 1.x.y e campos tipados. PyYAML é a dependência necessária para o formato YAML solicitado; `types-PyYAML` serve apenas à checagem estática de desenvolvimento. A criação da execução grava a definição normalizada e seu hash SHA-256 no SQLite. Uma leitura verifica esse hash. Repetições adversariais terão contrato próprio na Fase 4, com limites e saída explícitos.

**Efeito:** uma alteração no arquivo não muda execuções existentes. O hash cobre a definição validada, não os bytes originais do YAML.

## ADR-010: Scheduler local e recuperação conservadora

**Estado:** aceito em 2026-10-18.

**Contexto:** o manager da Fase 2 controla concorrência no processo e efeitos externos podem ocorrer antes de o workflow registrar seu resultado.

**Decisão:** o motor agenda lotes até `max_parallel_steps`; o manager mantém limite próprio e trava por workspace. Só validadores Python registrados podem repetir tentativas. Agentes e aprovações têm uma tentativa. Ao reiniciar, resultado final conhecido é reconciliado; resultado desconhecido marca `INTERRUPTED` sem reexecutar. A instalação usa um manager ativo por banco.

**Efeito:** não há retomada automática de ações externas incertas, coordenação entre processos ou outbox transacional entre ACP e workflow. O limite total interrompe lotes ativos no processo e é conferido ao consultar uma aprovação pendente. Diretórios separados por agente permitem paralelismo real; o cancelamento de tarefa ativa vale apenas no processo local.

## ADR-011: Aprovação humana local e condições tipadas

**Estado:** aceito em 2026-10-18.

**Contexto:** uma mensagem de agente não comprova aprovação, e não existe autenticação humana confiável no núcleo local.

**Decisão:** `human_approval` grava pedido e suspende o workflow. Uma chamada Python `decide()` exige que o identificador do ator conste de `local_approvers` e registra aprovação ou rejeição. Condições comparam apenas o booleano `passed` de uma validação registrada; texto livre não altera a política.

**Efeito:** aprovação e eventos ficam auditáveis no SQLite. A lista de atores é uma autorização local fornecida pelo código confiável, sem prova criptográfica de identidade; a aplicação deve permanecer em ambiente local supervisionado.
