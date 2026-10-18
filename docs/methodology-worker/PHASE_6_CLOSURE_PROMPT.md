# Prompt de fechamento da Fase 6

Execute o fechamento da Fase 6 do Agentic Engineering Core no checkout atual. O objetivo é validar o núcleo local multiprocesso, bloquear execução de código não confiável sem isolamento adequado e acrescentar um benchmark metodológico mínimo. Não inicie a Fase 7.

## Leia e confirme o estado inicial

Leia `AGENTS.md`, `README.md`, `docs/architecture/ARCHITECTURE.md`, `docs/architecture/DECISIONS.md`, `docs/ROADMAP.md` e a documentação das Fases 1–6 antes de alterar contratos. Inspecione os testes e as saídas em `.audit/verification.txt` e `.audit/real-codex-verification.txt`. Reexecute os testes necessários para confirmar o estado do checkout. Registre divergências entre código, documentação e resultados. A Fase 6 está funcional no ambiente local supervisionado; não a descreva como integralmente validada antes de executar os cenários abaixo.

Preserve ACP, Persistence Layer, Workflow Engine, Execution Manager, Documentation Worker e Governance Layer. Faça mudanças pequenas nas interfaces existentes. Mantenha o Codex CLI como executor opcional, sem chave de API paga. Não leia nem altere credenciais ou arquivos de sessão do Codex. Não aprove, publique ou migre metodologias reais.

## 6.1 Hardening multiprocesso, P0

Crie testes de integração com **dois processos Python independentes** usando o mesmo banco SQLite e diretórios locais. Sincronize os processos por barreiras explícitas, pipes ou eventos persistidos; não faça a correção depender apenas de `sleep`.

Cubra os casos abaixo:

1. Um processo publica mensagens ACP enquanto o outro as reivindica e confirma. Confirme correlação, autorização, recibo e estado persistido.
2. Dois workers disputam a mesma entrega. Confirme que apenas o dono do lease vigente pode confirmar e que o efeito associado a um recibo confirmado não se duplica.
3. Dois managers disputam o mesmo workspace e dois controllers disputam a mesma execução. Confirme serialização, transições válidas e ausência de turnos de agente duplicados.
4. Encerre abruptamente um processo após adquirir uma entrega e antes do ACK. Após a expiração do lease, outro processo deve recuperar a mensagem pendente sem corromper o histórico.
5. Encerre abruptamente um processo durante uma execução externa simulada. Se o resultado externo for desconhecido, confirme `INTERRUPTED` e ausência de repetição automática. Se o resultado já estiver persistido, confirme reconciliação sem novo efeito.
6. Faça uma disputa de publicação da mesma versão metodológica. Confirme uma única publicação, versão anterior intacta e estado de aprovação coerente.

Use adapters simulados e efeitos observáveis contados em armazenamento durável. Diferencie entrega ACP *at-least-once* de execução externa: não prometa exatamente uma execução para um efeito externo cujo resultado se perdeu. Corrija defeitos encontrados na causa comum e acrescente testes de falha antes de declarar o item concluído.

## 6.2 Benchmark Runner mínimo, P1

Estenda o `MethodologyEvaluator` com um executor local controlado que obtenha métricas de workflows executados pelo AEC, sem receber números de desempenho arbitrários do chamador. Use as interfaces existentes de workflow, execução e persistência. Um adapter simulado basta para a primeira comparação controlada. A candidata pode ser avaliada em ambiente temporário sem ser publicada ou se tornar ativa.

Defina um pequeno conjunto versionado de tarefas de referência. Para cada par base e candidata, fixe a tarefa, os critérios de aceite e seu hash, os gates, o adapter, os limites e as condições de execução. Use bancos e diretórios separados. Colete aprovação nos gates, número de correções, duração e chamadas de agentes; marque métricas ausentes como indisponíveis. Registre IDs de execução, versões, hashes, ambiente, resultados por tarefa e falhas do runner.

Execute mais de uma tarefa e, quando o custo permitir, repetições. Separe fixtures sintéticas de medições executadas. Relate amostra, variabilidade, regressões e limitações. Uma comparação pequena é exploratória; não declare causalidade ou superioridade geral. Se o runner falhar ou faltar evidência, devolva `INCONCLUSIVE` ou `FAIL` conforme o contrato, nunca `PASS` por ausência de dados. Mantenha avaliação e publicação independentes e preserve a aprovação humana obrigatória.

## 6.3 Segurança operacional, P0

Identifique todos os caminhos que podem executar código ou comandos do projeto, inclusive Codex CLI e `CommandGate`. Defina uma classificação de confiança fornecida pelo operador local confiável, nunca por payload de agente ou conteúdo do repositório. Bloqueie a execução de projetos ou comandos classificados como não confiáveis enquanto não houver sandbox de sistema operacional adequado. Teste a rejeição e o caminho local confiável já suportado. Documente o limite: worktree, `cwd` e locks não constituem confinamento de processos. Não implemente um sandbox incompleto apenas para marcar este item como concluído.

Mantenha `INTERRUPTED` para resultados externos desconhecidos. Confirme que apenas o código local confiável pode invocar decisões humanas e que um nome recebido de agente ou interface não autenticada não basta como aprovação. Documente os requisitos de autenticação remota e a limitação de `local_approvers`; não implemente aprovação pela rede ou celular nesta etapa.

## Verificação e entrega

Execute `uv run ruff check .`, `uv run mypy` e `uv run pytest`. Execute separadamente testes opt-in com Codex real apenas quando forem necessários para verificar uma mudança no adapter; registre o consumo e o resultado, sem expor sessão ou credenciais. Atualize o roadmap, a documentação e `docs/architecture/DECISIONS.md` se mudar contratos ou políticas.

Entregue diagnóstico inicial, mudanças por item F6H, evidências dos processos e do benchmark, resultados reais dos testes, riscos restantes e decisão fundamentada sobre iniciar a Fase 7. Não faça merge nem publique metodologia sem aprovação humana explícita.
