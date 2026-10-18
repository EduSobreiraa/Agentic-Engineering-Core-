# Roadmap do AEC

Cada item tem um identificador estável para vincular issues, commits e evidências. Marque um item como concluído somente quando houver artefato ou verificação correspondente. As fases seguem a ordem de dependência abaixo; itens de fases posteriores ainda não foram iniciados.

## Fase 0: fundação

- [x] F0-01 Analisar o repositório e o ambiente. Evidência: repositório vazio no início; Fedora 44, Python 3.14, `uv` e Codex CLI disponíveis.
- [x] F0-02 Definir a estrutura inicial do pacote Python. Evidência: `src/aec/` e `tests/`.
- [x] F0-03 Configurar `pyproject.toml`.
- [x] F0-04 Criar `README.md` e `AGENTS.md`.
- [x] F0-05 Registrar arquitetura, componentes e decisões em `docs/architecture/`.
- [x] F0-06 Definir convenções de código, testes e documentação em `AGENTS.md`.
- [x] F0-07 Preparar e verificar lint, type checking e pytest. Evidência: comandos do README.

**Conclusão:** repositório estruturado e verificável, sem lógica de orquestração.

## Fase 1: ACP v0.1

- [x] F1-01 Especificar o protocolo ACP v0.1. Evidência: `docs/protocols/ACP_SPEC.md`.
- [x] F1-02 Definir o envelope das mensagens. Evidência: `communication/schemas.py`.
- [x] F1-03 Definir tipos de mensagens. Evidência: `MESSAGE_TYPES.md`.
- [x] F1-04 Definir contratos de payload. Evidência: `communication/schemas.py`.
- [x] F1-05 Implementar modelos Pydantic. Evidência: testes de contratos.
- [x] F1-06 Definir identidade e autorização dos agentes. Evidência: `security/`.
- [x] F1-07 Implementar broker local. Evidência: `communication/broker.py`.
- [x] F1-08 Implementar persistência em SQLite. Evidência: migração v1.
- [x] F1-09 Implementar entrega, ACK e tratamento de erros. Evidência: testes de broker.
- [x] F1-10 Implementar deduplicação e processamento idempotente. Evidência: recibos únicos e testes de republicação.
- [x] F1-11 Criar testes unitários e de integração. Evidência: `tests/`.
- [x] F1-12 Criar demonstração entre agentes simulados. Evidência: `simulation/agents.py`.

**Conclusão:** orquestrador, implementador, QA e quality gate simulados trocam mensagens estruturadas com persistência, recuperação e validação de permissões. A confirmação do recibo não garante execução exatamente uma vez de efeitos externos.

## Fase 2: execução

- [x] F2-01 Definir interface abstrata dos executores. Evidência: `execution/base.py`.
- [x] F2-02 Implementar adapter Codex CLI. Evidência: `execution/adapters/codex_cli.py` e testes com executável falso.
- [x] F2-03 Verificar autenticação pela assinatura existente. Evidência: `codex login status` informou login pelo ChatGPT em 2026-10-17.
- [x] F2-04 Executar tarefas não interativas. Evidência: `exec --json` confirmado na CLI 0.163.0; dois turnos reais read-only no demo de workflow em 2026-10-18.
- [x] F2-05 Capturar respostas e resultados estruturados. Evidência: testes do parser JSONL e resultados reais persistidos no run `5df4cc7c-48cd-4d4b-90c5-5e5db4a0e0d3`.
- [x] F2-06 Implementar timeouts e tratamento de falhas. Evidência: testes de timeout e estados.
- [x] F2-07 Investigar persistência e retomada de sessões Codex. Evidência: `agent_sessions`, teste do adapter e dois turnos Codex reais somente leitura com o mesmo vínculo interno de sessão em 2026-10-18.
- [x] F2-08 Implementar isolamento de diretórios e worktrees. O manager confina diretórios às raízes autorizadas e usa travas locais por execução e workspace entre processos; o ciclo adversarial cria worktrees automaticamente. Evidência: testes de dois managers e de um segundo processo em `tests/integration/test_execution.py`.
- [x] F2-09 Testar troca de resultados entre duas execuções Codex. Evidência: cenário ACP com executável falso e troca real de plano para implementação no run `5df4cc7c-48cd-4d4b-90c5-5e5db4a0e0d3`.

**Estado:** execução, troca via ACP e retomada de sessão verificadas com Codex real somente leitura. O manager coordena execuções em um sistema de arquivos local; criação de worktrees pertence à metodologia adversarial. Isolamento de segurança em nível de sistema operacional e coordenação de workflows inteiros entre processos não são fornecidos.

## Fase 3: workflows

- [x] F3-01 Definir contratos de workflows. Evidência: `workflows/models.py`.
- [x] F3-02 Implementar carregamento de YAML. Evidência: `workflows/loader.py` e `docs/workflows/example.yaml`.
- [x] F3-03 Validar versões e esquemas. Evidência: `validate()` e testes de definições inválidas.
- [x] F3-04 Construir máquina de estados. Evidência: `workflows/state_machine.py` e eventos persistidos.
- [x] F3-05 Implementar transições condicionais. Evidência: condição booleana sobre resultado de validador.
- [x] F3-06 Implementar limites de iteração. Evidência: `AdversarialLoop` persiste e fixa `max_iterations`; workflows da Fase 3 continuam acíclicos.
- [x] F3-07 Implementar retries seguros. Evidência: tentativas limitadas de validadores registrados; agentes não são repetidos automaticamente.
- [x] F3-08 Implementar regras de interrupção. Evidência: timeout, cancelamento e estado `INTERRUPTED` para resultado incerto.
- [x] F3-09 Recuperar workflows após falhas. Evidência: `recover()` reconcilia resultados conhecidos e preserva aprovação pendente.

**Estado:** motor local de workflows acíclicos, versionados e persistentes implementado com adapter simulado e verificado com dois turnos Codex reais até `WAITING_APPROVAL`. Coordenação entre processos permanece pendente. A Fase 4 acrescentou o ciclo Implementador e QA como metodologia especializada.

## Fase 4: ciclo adversarial de engenharia

- [x] F4-01 Criar contrato do Implementador. Evidência: `engineering/models.py`.
- [x] F4-02 Criar contrato do QA adversarial. Evidência: `QAReviewResult` e `ReviewFinding`.
- [x] F4-03 Definir pacotes de contexto. Evidência: `engineering/loop.py` e `CONTEXT_PACKAGES.md`.
- [x] F4-04 Implementar submissão de artefatos. Evidência: commit capturado e referências ACP verificadas.
- [x] F4-05 Implementar revisão independente. Evidência: worktree QA e cenário simulado.
- [x] F4-06 Implementar relatório estruturado de falhas. Evidência: findings persistidos em SQLite e ACP.
- [x] F4-07 Implementar ciclo de correção. Evidência: cenário de duas iterações e limite.
- [x] F4-08 Integrar testes determinísticos. Evidência: `CommandGate` e cenário `unittest`.
- [x] F4-09 Bloquear conclusão quando houver falhas críticas. Evidência: testes de limite e gate reprovado.
- [x] F4-10 Exigir aprovação humana antes do merge. Evidência: `WAITING_APPROVAL` e teste de autorização.
- [x] F4-11 Executar experimento completo em repositório de teste. Evidência: cenário simulado em `tests/scenarios/test_adversarial_loop.py` e [execução com quatro turnos Codex reais](engineering/REAL_CODEX_EXPERIMENT.md), encerrada em `WAITING_APPROVAL`.

**Estado:** Implementador, QA, correção e validação funcionaram em uma execução real com Codex; o pedido de aprovação permaneceu pendente, sem decisão humana. Um teste de falha após o commit do controller confirmou recuperação sem repetir o agente. Isolamento de sistema operacional e resultados externos ainda desconhecidos continuam pendentes.

## Fase 5: documentação

- [x] F5-01 Criar Worker de Documentação. Evidência: `src/aec/documentation/worker.py`, contratos e rotas ACP testados.
- [x] F5-02 Gerar relatórios de execução. Evidência: template determinístico, `documentation_records` e cenário de duas iterações em `tests/integration/test_documentation.py`.
- [x] F5-03 Registrar decisões arquiteturais durante execuções. Evidência: `adr_records`, revisões vinculadas a `engineering_runs`, hash conferido e aprovação humana ligada ao ADR e à execução.
- [x] F5-04 Gerar histórico de alterações. Evidência: `change_summary()` usa commits e arquivos de `ImplementationResult` por iteração.
- [x] F5-05 Vincular evidências aos relatórios. Evidência: `source_refs` ACP, matriz de rastreabilidade, verificação de arquivos e SHA-256.
- [x] F5-06 Integrar documentação ao fechamento dos workflows. Evidência: validador registrado em um passo real do Workflow Engine no teste de integração; falha documental não altera o estado da engenharia.

**Conclusão do MVP determinístico:** o worker gera e recupera relatórios históricos para execuções aprovadas ou bloqueadas, registra revisões de ADR e publica resultados pelo ACP. A documentação técnica evolutiva é renderizada como proposta supervisionada; a escrita no projeto de destino permanece fora do worker. Não houve execução real do Codex CLI na verificação desta fase. O núcleo local ainda pressupõe um writer por banco e não autentica humanos entre máquinas.

## Fase 6: governança metodológica

- [x] F6-01 Criar Worker de Metodologia. Evidência: `methodology/worker.py` e cenário simulado.
- [x] F6-02 Implementar métricas de execução. Evidência: `methodology/metrics.py`; completude documental é calculada quando a versão declara `required_documents`, com verificação do artefato.
- [x] F6-03 Produzir propostas metodológicas em RFCs. Evidência: `DocumentationWorker.create_methodology_document()`, registro imutável e cenário simulado.
- [x] F6-04 Definir avaliação comparativa de metodologias. Evidência: pares tipados e cenário com fixtures; execução automática de benchmarks pendente.
- [x] F6-05 Implementar aprovação humana. Evidência: `MethodologyGovernance.decide()` e testes de rejeição.
- [x] F6-06 Publicar novas versões somente após aprovação. Evidência: publicação transacional e `WorkflowEngine.create_published()`; seleção pelo controlador especializado da Fase 4 pendente.
- [x] F6-07 Preservar histórico das decisões. Evidência: `methodology_events`, hashes, versões e vínculos de execução.

**Estado:** o ciclo supervisionado funciona com fixtures e workflows declarativos, integrado ao Documentation Worker da Fase 5. A Fase 6 ainda não conclui a integração com o `AdversarialLoop` nem executa benchmarks controlados por conta própria. Versões sem manifesto documental deixam essa métrica indisponível.

## Fase 7: expansão metodológica

- [ ] F7-01 Implementar Implementation Arena.
- [ ] F7-02 Implementar TDD com agentes especializados.
- [ ] F7-03 Implementar auditoria técnica Red Team e Blue Team.
- [ ] F7-04 Implementar decisão adversarial.
- [ ] F7-05 Integrar skills pstack de forma opcional, após verificar disponibilidade e contratos.
- [ ] F7-06 Selecionar metodologias automaticamente por regras.

## Fase 8: integrações e observabilidade

- [ ] F8-01 Investigar integração programática com Orca.
- [ ] F8-02 Investigar supervisão móvel de workflows.
- [ ] F8-03 Adicionar executor Ollama.
- [ ] F8-04 Implementar métricas consolidadas.
- [ ] F8-05 Comparar qualidade entre metodologias.
- [ ] F8-06 Avaliar suporte futuro a ambientes distribuídos.
