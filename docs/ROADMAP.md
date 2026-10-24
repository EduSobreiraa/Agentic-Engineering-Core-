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
- [x] F2-07 Investigar persistência e retomada de sessões Codex. Evidência: `agent_sessions`, teste do adapter e `test_real_session_resume_in_non_git_directory`, que executa dois turnos Codex reais somente leitura com o mesmo ID de sessão.
- [x] F2-08 Implementar isolamento de diretórios e worktrees. O manager confina diretórios às raízes autorizadas e usa travas locais por execução e workspace entre processos; o ciclo adversarial cria worktrees automaticamente. Evidência: testes de dois managers no mesmo processo e da primitiva de trava em um segundo processo em `tests/integration/test_execution.py`.
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

**Estado:** motor local de workflows acíclicos, versionados e persistentes implementado com adapter simulado e verificado com dois turnos Codex reais até `WAITING_APPROVAL`. Travas locais impedem dois motores de executar o mesmo workflow ao mesmo tempo; não há coordenação distribuída. A Fase 4 acrescentou o ciclo Implementador e QA como metodologia especializada.

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

**Estado:** Implementador, QA, correção e validação funcionaram em uma execução real com Codex; o pedido de aprovação permaneceu pendente, sem decisão humana. Um teste de falha após o commit do controller confirmou recuperação sem repetir o agente. Travas locais impedem dois controllers de executar o mesmo run. Isolamento de sistema operacional e resultados externos ainda desconhecidos continuam pendentes.

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
- [x] F6-04 Definir avaliação comparativa de metodologias. Evidência: pares tipados, cenário com fixtures e runner local de workflows com evidência persistida.
- [x] F6-05 Implementar aprovação humana. Evidência: `MethodologyGovernance.decide()` e testes de rejeição.
- [x] F6-06 Publicar novas versões somente após aprovação. Evidência: publicação transacional, `WorkflowEngine.create_published()` e seleção de versão publicada compatível pelo `AdversarialLoop`, com gate anterior ao QA e vínculo persistido.
- [x] F6-07 Preservar histórico das decisões. Evidência: `methodology_events`, hashes, versões e vínculos de execução.

**Estado:** funcional no ambiente local supervisionado. O ciclo integra o Documentation Worker da Fase 5 e seleciona versões publicadas compatíveis no `AdversarialLoop`. O benchmark local executa workflows com adapter roteirizado; seus resultados são exploratórios e não medem retrabalho. Versões sem manifesto documental deixam a completude indisponível. A verificação anterior está em `.audit/verification.txt` e `.audit/real-codex-verification.txt`; a verificação do fechamento está em `.audit/f6-hardening-verification.txt`.

## Fechamento da Fase 6 antes da Fase 7

- [x] F6H-01 Hardening multiprocesso (P0). Testes com processos Python independentes, SQLite compartilhado, ACP, manager, workflow, publicação, falha abrupta e recuperação; resultado externo incerto continua `INTERRUPTED`. Evidência: `tests/integration/test_multiprocess.py`.
- [x] F6H-02 Benchmark Runner mínimo (P1). Tarefas roteirizadas equivalentes, critérios e condições fixados, ambientes separados e métricas obtidas de workflows executados. Evidência: `methodology/benchmark.py` e `tests/integration/test_methodology_benchmark.py`. Resultados pequenos são exploratórios.
- [x] F6H-03 Segurança operacional (P0). Política explícita de raízes locais confiáveis nos pontos de execução. Projetos externos não declarados são bloqueados. O limite do controle está em `docs/execution/SECURITY.md`.
- [x] F6H-04 Identidade remota (P2). Requisitos registrados em `docs/methodology-worker/GOVERNANCE.md`; implementação de rede permanece fora do escopo.

O [prompt de fechamento](methodology-worker/PHASE_6_CLOSURE_PROMPT.md) detalha o escopo e os critérios de aceite.

## Fase 7: expansão metodológica

- [x] F7-01 Implementar Implementation Arena para projetos locais confiáveis. A versão publicada `1.0.1` mediu cinco amostras alternadas de tempo e memória com Codex real: empate entre soluções válidas e seleção por falha de gate controlada, seguida de QA real e aprovação do run pendente. Superioridade natural por desempenho ainda não foi demonstrada; código não confiável continua bloqueado.
- [x] F7-02 Implementar TDD com agentes especializados. A versão publicada `1.0.1` percorreu Red, Green, Refactor, regressão e QA com Codex real; `TDDChecks` preserva os testes e captura o commit imutável quando configurado com caminhos permitidos. O run real permaneceu em `WAITING_APPROVAL`.
- [x] F7-03 Implementar auditoria técnica estática Red Team e Blue Team em fixture local confiável. A versão publicada `1.0.3` percorreu finding, correção e reteste na mesma execução Codex; AEC-SEC-001 vincula aprovação e conclusão ao snapshot. `1.0.2` permanece bloqueada. Testes perigosos aguardam sandbox.
- [x] F7-04 Implementar decisão adversarial. A versão publicada `1.0.1` percorreu proposta, contestação, avaliação e ADR proposto com Codex real; a decisão do run real permaneceu pendente.
- [x] F7-05 Preparar interface opcional de skills externas. Referência local explícita com hash implementada; execução ou importação automática de skills não fazem parte deste marco.
- [x] F7-06 Selecionar metodologias automaticamente por regras. Router determinístico consulta versões publicadas, dependências, recursos e política de escopo; testes com fixtures em `tests/unit/test_methodology_routing.py`.
- [x] F7-07 Persistir entradas dos workflows publicados. Requisitos, critérios e referências são gravados com hash na criação atômica, recuperados após reinício e incluídos em prompts ACP e relatórios; referências externas exigem verificação de integridade antes de uso.
- [x] F7-08 Validar desenvolvimento adversarial local com Codex real. Implementador, gates e QA chegaram a `WAITING_APPROVAL` em repositório descartável; nenhuma aprovação ou merge foi realizado. A Arena real validou o ramo de empate.

**Estado da Fase 7:** escopo funcional local supervisionado concluído; diagnóstico, ensaios reais, propostas publicadas e limites estão em [PHASE_7.md](methodologies/PHASE_7.md). A segurança operacional ampliada continua bloqueada sem sandbox de sistema operacional e identidade remota. Nenhuma aprovação de run ou merge foi inferida da publicação das metodologias.

**Marco A — funcional local supervisionado:** o TOCTOU da auditoria tornou `security-audit@1.0.2` inadequada para publicação. AEC-SEC-001 adiciona commit, snapshot imutável e vínculo do digest à aprovação e à conclusão em `1.0.3`. As quatro revisões da Fase 7 foram aprovadas pela identidade local autorizada e publicadas pelo Registry. AEC-RES-002 demonstrou recuperação conservadora após `SIGKILL` real do manager; isso não prova isolamento de código não confiável.

**AEC-RES-002 — reconciliação de processos:** [x] regressão real final em diretório descartável confirmou Codex ativo após `SIGKILL`, identidade completa persistida, recuperação `INTERRUPTED`, bloqueio de novo turno e recusa de reconciliação com grupo ativo. [x] callback por pedido cobre adapters tardios; filho remanescente e falha de registro mantêm quarentena. [x] simulação de reboot preserva inspeção obrigatória; PID reutilizado no mesmo boot mantém bloqueio conservador. A reconciliação registra operador, evidência e estado dos efeitos externos. [ ] validar supervisão confiável da árvore completa e encerramento por cgroup ou escopo equivalente antes de automatizar terminação; descendentes fora do grupo e identidade remota continuam fora da garantia.

**AEC-GOV-003 — revisão metodológica completa:** [x] contrato com candidata integral e digest canônico, diff semântico, checks independentes, vínculo com execuções e evidências persistidas, aprovação local pelo digest e publicação transacional. [x] testes de alteração da candidata e da evidência após avaliação, identidade não autorizada, versão bloqueada e publicação idempotente em banco descartável. AEC-PUB-004 publicou `security-audit@1.0.3` após autorização explícita do usuário e conferência da evidência externa.

**AEC-PUB-004/005 — publicação local:** [x] ensaios Codex em repositórios descartáveis preservados; [x] registro por referência verificável da origem, sem cópia direta de linhas SQLite; [x] propostas avaliadas e documentadas no banco local persistente; [x] decisão humana vinculada ao digest e publicação transacional das quatro revisões; [x] regressão após publicação, com 201 testes aprovados e 11 opt-in pulados. A origem e os projetos Git precisam permanecer acessíveis para novas conferências das evidências externas.

**Marco B — segurança operacional ampliada:** sandbox de sistema operacional, testes adversariais perigosos e identidade remota continuam bloqueados e não são requisitos para ensaios locais confiáveis do Marco A.

**Hardening local após a Fase 7:** [x] modo opcional de supervisão por scope `systemd --user`, com referência persistida antes do lançamento, consulta fail-closed, teste de filho fora do PGID, `SIGKILL` do manager e turno Codex real somente leitura. [x] gateway local por socket Unix com identidade derivada de `SO_PEERCRED` e permissões por ação, testado com fixtures. [ ] implantação do gateway sob conta de serviço separada, com proteção do SQLite e artefatos, e execução dos agentes sem acesso ao UID/socket do aprovador; o ambiente atual não demonstra essa separação. [ ] sandbox para código não confiável e autenticação remota. Evidência: `.audit/local-supervision-verification.txt`.

## Fase 8: integrações e observabilidade

- [ ] F8-01 Investigar integração programática com Orca.
- [ ] F8-02 Investigar supervisão móvel de workflows.
- [ ] F8-03 Adicionar executor Ollama.
- [ ] F8-04 Implementar métricas consolidadas.
- [ ] F8-05 Comparar qualidade entre metodologias.
- [ ] F8-06 Avaliar suporte futuro a ambientes distribuídos.
