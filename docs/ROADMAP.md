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
- [x] F2-04 Executar tarefas não interativas. Evidência: `exec --json` confirmado na CLI 0.163.0 e executado com falso Codex; turno real pendente.
- [x] F2-05 Capturar respostas e resultados estruturados. Evidência: testes do parser JSONL; evento real pendente.
- [x] F2-06 Implementar timeouts e tratamento de falhas. Evidência: testes de timeout e estados.
- [x] F2-07 Investigar persistência e retomada de sessões Codex. Evidência: `exec resume --help`, tabela `agent_sessions`; retomada real pendente.
- [ ] F2-08 Implementar isolamento de diretórios e worktrees. Diretórios distintos e trava por manager implementados; worktrees automáticos e coordenação entre processos pendentes.
- [x] F2-09 Testar troca de resultados entre duas execuções Codex. Evidência: cenário ACP com executável falso; cenário real opt-in pendente.

**Estado:** infraestrutura implementada e verificada com Codex falso. A conclusão da fase depende de um turno real opt-in e da validação de retomada e isolamento no ambiente real.

## Fase 3: workflows

- [ ] F3-01 Definir contratos de workflows.
- [ ] F3-02 Implementar carregamento de YAML.
- [ ] F3-03 Validar versões e esquemas.
- [ ] F3-04 Construir máquina de estados.
- [ ] F3-05 Implementar transições condicionais.
- [ ] F3-06 Implementar limites de iteração.
- [ ] F3-07 Implementar retries seguros.
- [ ] F3-08 Implementar regras de interrupção.
- [ ] F3-09 Recuperar workflows após falhas.

**Conclusão:** workflows determinísticos executam a partir de definições versionadas.

## Fase 4: ciclo adversarial de engenharia

- [ ] F4-01 Criar contrato do Implementador.
- [ ] F4-02 Criar contrato do QA adversarial.
- [ ] F4-03 Definir pacotes de contexto.
- [ ] F4-04 Implementar submissão de artefatos.
- [ ] F4-05 Implementar revisão independente.
- [ ] F4-06 Implementar relatório estruturado de falhas.
- [ ] F4-07 Implementar ciclo de correção.
- [ ] F4-08 Integrar testes determinísticos.
- [ ] F4-09 Bloquear conclusão quando houver falhas críticas.
- [ ] F4-10 Exigir aprovação humana antes do merge.
- [ ] F4-11 Executar experimento completo em repositório de teste.

**Conclusão:** Implementador, QA, correção, validação e aprovação funcionam sem cópia manual de mensagens.

## Fase 5: documentação

- [ ] F5-01 Criar Worker de Documentação.
- [ ] F5-02 Gerar relatórios de execução.
- [ ] F5-03 Registrar decisões arquiteturais durante execuções.
- [ ] F5-04 Gerar histórico de alterações.
- [ ] F5-05 Vincular evidências aos relatórios.
- [ ] F5-06 Integrar documentação ao fechamento dos workflows.

## Fase 6: governança metodológica

- [ ] F6-01 Criar Worker de Metodologia.
- [ ] F6-02 Implementar métricas de execução.
- [ ] F6-03 Produzir propostas metodológicas em RFCs.
- [ ] F6-04 Definir avaliação comparativa de metodologias.
- [ ] F6-05 Implementar aprovação humana.
- [ ] F6-06 Publicar novas versões somente após aprovação.
- [ ] F6-07 Preservar histórico das decisões.

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
