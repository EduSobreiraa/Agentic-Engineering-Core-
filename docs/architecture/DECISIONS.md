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
