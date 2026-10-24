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

## ADR-012: Ciclo adversarial como metodologia especializada

**Estado:** aceito em 2026-10-18.

**Contexto:** o Workflow Engine da Fase 3 exige DAGs acíclicos, mas a metodologia adversarial precisa repetir Implementador e QA com limite e persistência.

**Decisão:** `AdversarialLoop` persiste a iteração e executa cada papel por um workflow `dev-adversarial@1.0.0` separado. O Execution Manager continua sendo a única ponte com adapters. Commits e worktrees locais são capturados pelo núcleo; o QA revisa snapshot separado. Gates são comandos registrados pelo projeto, e o pedido de aprovação usa o passo humano da Fase 3. Critérios, limite e configuração de gates são fixados na execução.

**Efeito:** outros workflows não precisam seguir essa metodologia. Worktrees não são isolamento de segurança em nível de sistema operacional. Resultado externo incerto bloqueia retomada automática; não há merge ou push automático. A integração real com dois Codex e recuperação de commits capturados imediatamente antes de falha SQLite exigem verificação posterior.

## ADR-013: Documentos históricos derivados de evidências tipadas

**Estado:** proposto na branch da Fase 5 em 2026-10-18; a revisão humana da mudança ainda está pendente.

**Contexto:** o ciclo adversarial já persiste implementações, revisões, gates e aprovações, mas não produz um registro documental recuperável. Declarações de agentes não comprovam testes nem decisões humanas.

**Decisão proposta:** o Worker de Documentação recebe pedidos ACP autorizados, coleta os registros existentes no SQLite e gera relatórios Markdown determinísticos. O mesmo banco guarda IDs, hashes e referências dos documentos históricos. `adr_records` guarda revisões de ADR vinculadas à execução. IDs de pedidos tornam o processamento idempotente. O worker não edita registros históricos, aprovações, políticas ou metodologias. ADRs aceitos exigem uma aprovação humana local vinculada ao próprio ADR.

**Efeito:** o ACP ganha mensagens documentais tipadas e rotas por papel. O Workflow Engine pode solicitar um relatório por um validador registrado, sem criar um executor paralelo. A integridade do arquivo pode ser comparada ao SHA-256 persistido. O limite de um writer por banco e a ausência de autenticação humana entre máquinas continuam válidos.

## ADR-014: Propostas imutáveis e publicação metodológica transacional

**Estado:** implementado na branch da Fase 6 em 2026-10-18; publicação desta mudança sujeita à revisão humana.

**Contexto:** o ACP transporta propostas, mas não autentica aprovações humanas. A Fase 5 já oferece um Worker de Documentação com artefatos imutáveis.

**Decisão:** propostas, avaliações, decisões, versões e vínculos são gravados no SQLite existente pela migração posterior às migrações da Fase 5. O hash da proposta fixa o conteúdo avaliado e aprovado. O worker apenas observa e propõe; `MethodologyGovernance` exige um ator autorizado em `local_approvers` e publica em uma transação. O único operador declarativo inicial adiciona `type_check` antes de QA. O Workflow Engine pode selecionar uma definição publicada por `create_published()`. O Worker de Documentação existente renderiza RFC, avaliação, decisão e publicação a partir dos registros persistidos, com hashes e histórico imutável.

**Efeito:** publicação rejeita versão-base obsoleta, dependência ausente e aprovação inválida. Repetir a mesma publicação não cria outra versão. A autenticação humana permanece local, conforme ADR-011. A adoção pelo `AdversarialLoop` e benchmarks executados pelo AEC continuam pendentes.

## ADR-015: Recuperação conservadora de snapshot de implementação

**Estado:** implementado em 2026-10-18.

**Contexto:** o controller pode criar um commit Git depois que o workflow do agente terminou e antes de registrar o novo head no SQLite. Nesse intervalo, a execução ficava bloqueada após reinício, apesar de o resultado do agente estar persistido.

**Decisão:** a retomada aceita somente um commit filho direto do head registrado, com mensagem e autoria do controller, worktree limpa, arquivos confinados aos caminhos permitidos, workflow e execução concluídos e artefatos íntegros. Ela reconstrói a linha da iteração e o head sem executar o agente novamente. Qualquer divergência permanece bloqueada.

**Efeito:** uma falha entre captura e persistência é recuperável. Resultados externos incertos e commits não atribuíveis ao controller continuam exigindo inspeção humana.

## ADR-016: Manifesto documental por versão metodológica

**Estado:** implementado em 2026-10-18.

**Contexto:** a Fase 5 produz três tipos de relatório, mas a métrica de completude não podia inferir quais eram obrigatórios para uma metodologia.

**Decisão:** `MethodologyVersion.required_documents` declara um subconjunto dos tipos documentais existentes. A observação conta apenas registros válidos com conteúdo e artefato íntegros vinculados à execução. Uma versão sem manifesto mantém a métrica indisponível, sem transformar ausência de requisito em zero.

**Efeito:** versões antigas continuam legíveis. Versões com manifesto produzem numerador e denominador determinísticos; o manifesto é herdado por uma versão candidata.

## ADR-017: Coordenação local de execuções entre managers

**Estado:** implementado em 2026-10-18; atualiza o limite operacional das ADR-008 e ADR-010.

**Contexto:** travas em memória não impediam que dois managers executassem no mesmo workspace, e um manager novo podia marcar como interrompido um turno ainda ativo no outro.

**Decisão:** cada execução mantém uma trava POSIX de arquivo até a persistência do resultado; cada workspace tem outra trava durante a chamada ao adapter. A reconciliação marca `INTERRUPTED` somente quando consegue adquirir a trava da execução. As travas ficam ao lado do SQLite e permitem paralelismo entre workspaces distintos. O controller adversarial cria worktrees; o manager genérico recebe um diretório já existente.

**Efeito:** managers no mesmo sistema de arquivos local não executam simultaneamente no mesmo workspace nem interrompem um turno com dono ativo. O scheduler de workflow e o controller adversarial não ganharam coordenação entre processos; cada run ainda requer um único scheduler ativo. Não há garantia de lock em armazenamento remoto ou Windows.

## ADR-018: Seleção de versões publicadas pelo controller adversarial

**Estado:** implementado em 2026-10-18.

**Contexto:** o Registry publicava `dev-adversarial@1.1.0`, mas o controller especializado continuava a executar sempre `1.0.0`, sem garantir que um gate proposto antes do QA fosse aplicado.

**Decisão:** o controller aceita uma versão publicada explicitamente selecionada somente quando a definição tem a topologia que ele implementa. Validadores declarados como dependências do QA rodam no snapshot antes da revisão, com resultados persistidos e mensagens ACP. A criação da execução grava o vínculo de versão na mesma transação. Execuções legadas sem seleção continuam em `1.0.0`; uma execução vinculada não aceita retomada por um controller configurado para outra versão.

**Efeito:** versões compatíveis podem ser adotadas após publicação supervisionada, sem mudar execuções antigas. Definições de outras topologias exigem outro controller ou o Workflow Engine genérico.

## ADR-019: Posse local por execução de workflow

**Estado:** implementado em 2026-10-18; complementa ADR-017.

**Contexto:** mesmo com execuções de agentes protegidas, dois motores podiam tentar recuperar ou avançar o mesmo workflow simultaneamente. Um segundo motor podia marcar como incerto um passo ainda ativo no primeiro.

**Decisão:** `WorkflowEngine.run()` mantém uma trava local por run durante o agendamento; `recover()` externo apenas lê enquanto a trava está ocupada. `AdversarialLoop.run()` mantém outra trava por execução de engenharia. Cancelamento vindo de outra instância durante a execução é recusado porque o adapter ativo pertence ao processo dono.

**Efeito:** uma segunda instância espera e não repete agentes. A coordenação depende de travas POSIX no mesmo sistema de arquivos e não fornece agendamento distribuído nem cancelamento remoto.

## ADR-020: Criação atômica de workflow metodológico

**Estado:** implementado em 2026-10-18.

**Contexto:** `WorkflowEngine.create_published()` criava um workflow e só depois gravava o vínculo com a versão publicada. Uma falha no segundo passo deixava uma execução sem versão metodológica registrada.

**Decisão:** o repositório de workflows grava o vínculo opcional na mesma transação SQLite que cria o run, seus passos e o evento inicial. O motor só passa esse vínculo depois de consultar uma versão publicada no Registry do mesmo banco.

**Efeito:** falha na gravação do vínculo desfaz a criação inteira. A execução já iniciada continua presa à definição e ao hash selecionados.

## ADR-021: Admissão explícita de projetos confiáveis

**Estado:** implementado em 2026-10-18.

**Contexto:** a execução de Codex CLI ou command gates em um checkout externo pode rodar código do projeto no host. As travas e o sandbox solicitado à CLI não isolam todas as operações do sistema operacional.

**Decisão:** o operador fornece uma `ExecutionPolicy` com raízes locais específicas e confiáveis. Manager, adapter, controlador adversarial e gates conferem o caminho resolvido antes de executar. Raízes genéricas ou sensíveis são recusadas. A política não confere autoridade a agentes nem substitui isolamento de sistema operacional.

**Efeito:** callers precisam fornecer a política explicitamente. Projetos desconhecidos ficam bloqueados. O AEC continua limitado a execução local supervisionada em projetos confiáveis; a política não protege contra código hostil dentro do próprio processo Python.

## ADR-022: Benchmark local executado e procedência da evidência

**Estado:** implementado em 2026-10-18.

**Contexto:** a comparação original aceitava números de um callback, úteis para fixtures, mas insuficientes para afirmar melhoria observada.

**Decisão:** um runner executa as definições base e candidata com Workflow Engine e SQLite separados por tarefa, repetição e versão. Critérios e condições são comparados por hash; resultados e caminhos dos bancos são gravados na avaliação. O contrato distingue `FIXTURE` de `EXECUTED`. O runner inicial usa adapter roteirizado, tarefas de tipagem e métricas diretamente observáveis. Revisões e retrabalho permanecem indisponíveis nele.

**Efeito:** o resultado executado pode detectar regressão de aceite e apoiar uma decisão humana exploratória. Não há inferência causal nem benchmark de desempenho de Codex. Avaliações `FIXTURE` não autorizam aprovação ou publicação; avaliações executadas continuam exigindo aprovação humana.

## ADR-023: Verificação local entre processos

**Estado:** verificado em 2026-10-18.

**Contexto:** testes em um processo não demonstravam a coordenação prevista nas ADR-017 e ADR-019.

**Decisão:** a suíte inicia processos Python independentes com `spawn`, SQLite compartilhado, concorrência de ACP, manager, workflow e publicação, além de saída abrupta após resultado conhecido e desconhecido. O segundo processo deve consumir mensagens pendentes e recuperar resultados persistidos sem duplicar efeitos. Resultado externo desconhecido permanece `INTERRUPTED`.

**Efeito:** a garantia foi verificada no sistema de arquivos local POSIX usado nos testes. Isso não constitui teste distribuído, de armazenamento remoto ou de isolamento contra código não confiável.

## ADR-024: Expansão declarativa com execução parcial supervisionada

**Estado:** implementado parcialmente em 2026-10-19.

**Contexto:** a Fase 7 precisa de mais metodologias sem criar outro orquestrador ou alterar o ACP. O Registry e o Workflow Engine já fixam definições publicadas por execução. Worktrees e locks locais não isolam código hostil.

**Decisão:** TDD, auditoria estática e decisão adversarial são `MethodologyVersion`s que usam passos do Workflow Engine. O Router seleciona apenas versões publicadas e aplica limites determinísticos, autorização local para override e `ExecutionPolicy` para auditoria. A Arena usa o mesmo motor em worktrees sequenciais e mantém a seleção pendente de QA e aprovação. Relatórios de workflows genéricos são produzidos pelo Documentation Worker a partir de registros persistidos; saídas de agentes são identificadas como declarações. Observações genéricas não inferem métricas que o registro não mede. A interface de skills externas registra apenas metadados e hash de um arquivo local escolhido pelo operador.

**Efeito:** cenários com fixtures podem ser verificados sem inferência paga. A Arena ainda não vincula suas definições específicas da tarefa a uma versão publicada, então o Router a mantém indisponível. O gate Red do TDD não entra em uma taxa agregada de gates positivos. Auditoria dinâmica, código não confiável, ADR formal de workflow genérico e aprovação remota continuam bloqueados. Execuções `INTERRUPTED` continuam sem repetição automática.

## ADR-025: Snapshot imutável do Red para TDD local

**Estado:** implementado em 2026-10-19.

**Contexto:** hashes de testes guardados só na memória impediam a conferência da etapa Green após reinício do processo. Reexecutar o Test Designer para reconstruí-los poderia repetir uma ação externa.

**Decisão:** `TDDChecks` grava os caminhos relativos e hashes dos testes obrigatórios em `tdd_red_snapshots`, sob o ID da execução. A linha é imutável e seu SHA-256 é conferido em Green e regressão. Uma segunda chamada de Red só aceita o mesmo snapshot. Ausência ou divergência falha o gate sem repetir o agente.

**Efeito:** o validador pode retomar a conferência dos testes com outro processo local. O comando de teste ainda requer projeto declarado confiável; a migração não cria sandbox nem autoriza execução automática após `INTERRUPTED`.

## ADR-026: Execução principal publicada da Arena

**Estado:** implementado com fixtures em 2026-10-19; atualiza o bloqueio de versão da ADR-024.

**Contexto:** a Arena persistia definições filhas com prompts específicos, mas a seleção final não tinha vínculo com uma versão publicada. O Router bloqueava a metodologia e o Documentation Worker não podia gerar seu relatório.

**Decisão:** `implementation-arena@1.0.0` define um workflow principal fixo com validação de seleção e aprovação. O controlador exige essa versão publicada antes de executar candidatos. Ele persiste uma comparação imutável com hash no SQLite e vincula o workflow principal pelo `create_published()`. Candidatos e QA continuam como workflows filhos com entradas da tarefa. O validador da seleção lê a comparação persistida; falha, empate ou QA não aprovado encerram o principal sem aprovação. O Documentation Worker usa a versão e a comparação verificadas para gerar `arena-report`.

**Efeito:** o Router pode escolher a Arena quando a versão estiver publicada e os três agentes, executor e validator estiverem disponíveis. A vinculação não oferece sandbox para impedir escrita cruzada por código hostil. Não houve execução com Codex real nem merge automático.

## ADR-027: Revisões de ADR para workflows de decisão

**Estado:** implementado com fixtures em 2026-10-19; atualiza o limite de ADR genérico da ADR-024.

**Contexto:** `adr_records` possui chave estrangeira para `engineering_runs`, enquanto a decisão adversarial usa `workflow_runs`. O renderer existente já exige uma aprovação humana para estados além de `PROPOSED`.

**Decisão:** uma nova tabela imutável guarda revisões de ADR ligadas a workflows publicados de `decision-adversarial`. `record_workflow_adr()` usa o mesmo contrato `ADR`, renderer, hash e consulta `get_adr()`. Ele exige que proposta, contestação, avaliação e gate tenham terminado. Uma revisão aceita ou rejeitada requer aprovação local vinculada à execução e um aprovador diferente do proponente. IDs de ADR não podem colidir entre as duas tabelas.

**Efeito:** o cenário de decisão produz ADR proposto e revisão aceita apenas após aprovação registrada. Registros de engenharia existentes permanecem intactos. A confiança na identidade humana continua local e nenhuma decisão é executada automaticamente.

## ADR-028: Auditoria estática sem correção executada

**Estado:** testado com fixtures em 2026-10-19.

**Contexto:** a Fase 7 exige findings rastreáveis, mas o AEC ainda não oferece sandbox de sistema operacional para testes de segurança ou correções em projetos não confiáveis.

**Decisão:** o workflow de auditoria mantém agentes em modo somente leitura. O Red Team usa `FINDING_REPORTED` do ACP com artefato persistido. O validador final recebe a proposta do Blue Team e, no cenário E003, confere uma fixture corrigida separada. Ele não interpreta a proposta como correção aplicada no projeto auditado.

**Efeito:** escopo não autorizado e operações perigosas continuam bloqueados. A evidência de fixture não autoriza afirmar que o Blue Team corrigiu um projeto real.

## ADR-029: Entradas imutáveis por execução publicada

**Estado:** implementado e testado após reinício em 2026-10-19.

**Contexto:** a definição metodológica publicada é imutável, mas seus prompts genéricos não preservavam requisitos, critérios de aceite e referências específicos da tarefa. Reconstruir esses dados da memória do processo impediria auditoria e recuperação.

**Decisão:** novas chamadas a `WorkflowEngine.create_published()` exigem um `WorkflowTaskInput` com requisitos e critérios não vazios. O repositório grava seu JSON e SHA-256 em `workflow_run_inputs`, na mesma transação que cria a execução e o vínculo com a versão publicada. A linha é imutável. Ao executar, o motor acrescenta a entrada persistida aos prompts dos agentes e às mensagens ACP sem alterar a definição publicada. O Documentation Worker inclui a entrada nos relatórios. A Arena grava a mesma entrada no workflow principal e preserva referências nos prompts e na comparação.

**Efeito:** outro processo pode recuperar o contexto original e conferir sua integridade. Execuções antigas sem entrada continuam legíveis. Referências de artefatos são metadados estruturados; seu hash armazenado não prova que os bytes externos ainda existem ou permanecem íntegros. Elas não são anexadas como `artifact_refs` de uma mensagem ACP de outra execução.

## ADR-030: Contrato explícito de saída para revisores em versões candidatas

**Estado:** candidatos `1.0.1` testados localmente em 2026-10-19 e publicados em 2026-10-23; os efeitos abaixo descrevem os ensaios anteriores à publicação.

**Contexto:** no TDD real, as etapas Red, Green e Refactor passaram, mas o QA da versão `1.0.0` respondeu em texto livre. O Execution Manager recusou a saída porque o ACP exige JSON estruturado para `ReviewRequest`. Contestador da decisão e Red Team da auditoria usam o mesmo contrato.

**Decisão:** preservar as definições `1.0.0` e preparar versões `1.0.1` que explicitem nos prompts dos revisores o JSON exigido. O TDD instrui o QA a revisar o commit atual; a auditoria limita o Red Team a evidência estática e passa seu finding ao Blue Team; a decisão exige contestação estruturada. Os testes registram essas versões apenas em bancos descartáveis.

**Efeito:** TDD, auditoria estática e decisão percorreram seus ramos reais com Codex e ficaram pendentes de aprovação humana local. A aprovação e publicação operacional das novas versões seguem o processo supervisionado da Fase 6; nenhum workflow existente recebe uma alteração silenciosa.

## ADR-031: Evitar bytecode no worker sem ampliar caminhos autorizados

**Estado:** implementado e testado em 2026-10-19.

**Contexto:** no demo adversarial, arquivos `__pycache__` gerados por Python fizeram `capture()` bloquear uma implementação cuja alteração pretendida era apenas `calc.py`. O controlador inclui arquivos ignorados pelo Git na conferência de caminhos, por proteção contra efeitos colaterais.

**Decisão:** processos Codex e comandos TDD executados pelo AEC recebem `PYTHONDONTWRITEBYTECODE=1` no ambiente do subprocesso. `capture()` e a lista de caminhos autorizados permanecem intactos. Um teste confirma que até um bytecode ignorado pelo Git continua bloqueado se aparecer fora do escopo.

**Efeito:** o demo real chegou a `WAITING_APPROVAL` sem variável manual no shell. Outros arquivos temporários ainda podem bloquear a captura e exigem inspeção; a configuração não constitui sandbox de sistema operacional.

## ADR-032: Snapshot de revisão TDD antes do QA

**Estado:** implementado e testado com Codex real em 2026-10-19.

**Contexto:** o primeiro ensaio TDD com QA estruturado dependia de um wrapper do teste para criar o commit revisado. `TDDChecks` não ligava o resultado do QA a um snapshot verificável da implementação após Refactor.

**Decisão:** quando o operador fornece `allowed_paths`, `TDDChecks.regression()` roda os testes, captura um commit Git limitado a esses caminhos e grava commit, arquivos e hashes dos testes em `tdd_review_snapshots`, imutável. `TDDChecks.qa()` exige workspace limpo, snapshot íntegro e `reviewed_commit` igual ao HEAD. O modo legado sem `allowed_paths` continua disponível para fixtures antigas.

**Efeito:** o ensaio real da candidata TDD `1.0.1` usou os validadores padrão, preservou Red e snapshot de revisão após reinício e chegou a `WAITING_APPROVAL`. Falha de gravação após o commit exige inspeção conservadora; não há repetição automática de agente. A versão candidata continua sem publicação operacional.

## ADR-033: Correção estática supervisionada no workflow de auditoria

**Estado:** candidata `1.0.2` testada localmente em 2026-10-19; publicação operacional pendente.

**Contexto:** a ADR-028 limitou a auditoria publicada à análise e proposta. O ensaio real de correção ocorreu em outro workflow, sem vínculo de dependência entre finding, alteração e reteste.

**Decisão:** preservar `security-audit@1.0.0` e a candidata `1.0.1`. A candidata `1.0.2` acrescenta um agente Blue com escrita autorizada após o gate da proposta, seguido por `audit_retest` antes da aprovação. Para a fixture local `unsafe.py`, `StaticEvalAuditChecks` exige projeto confiável, vulnerabilidade direta inicial, Git HEAD estável, alteração apenas no arquivo autorizado, remoção da chamada direta a `eval`, importação de `ast` e presença de `ast.literal_eval` no AST. O código vulnerável não é executado. O Documentation Worker registra correção e reteste no relatório da mesma execução.

**Efeito:** finding, proposta, correção e reteste são rastreados por um único workflow publicado no banco descartável de ensaio. O reteste é específico para esta fixture e não valida segurança geral da implementação. A publicação operacional requer aprovação humana; auditorias perigosas e projetos não confiáveis continuam bloqueados sem sandbox de sistema operacional.

## ADR-034: Interrupção controlada de um turno Codex real

**Estado:** ensaiado localmente em 2026-10-19.

**Contexto:** os testes de recuperação existentes usam adapters simulados ou processos de teste, mas não interrompiam uma invocação Codex real.

**Decisão:** um ensaio opt-in inicia um turno Codex somente leitura em diretório descartável, confirma que o subprocesso permanece ativo, cancela a tarefa e recria o Execution Manager. A recuperação mantém o workflow `INTERRUPTED`, recusa `run()` e conserva uma única linha de execução. Não há retry automático.

**Efeito:** o ensaio comprova o caminho de cancelamento cooperativo, não a perda abrupta do processo nem a ausência de efeitos externos desconhecidos. Um resultado externo incerto ainda exige reconciliação humana.

## ADR-035: AEC-SEC-001 — aprovação vinculada ao artefato validado

**Estado:** hotfix implementado e testado com fixtures em 2026-10-19; `security-audit@1.0.3` publicada em 2026-10-23 após ensaio real e aprovação metodológica local.

**Contexto:** em `security-audit@1.0.2`, o reteste guardava um booleano. Após um reteste positivo, uma alteração em `unsafe.py` podia manter o gate verdadeiro e levar o workflow a `COMPLETED` com bytes diferentes. O hash do relatório não cobria o arquivo. A versão `1.0.2` fica bloqueada para roteamento e publicação pela governança.

**Decisão:** preservar as definições anteriores e criar a candidata `1.0.3`. O reteste captura apenas `unsafe.py` em um commit Git, lê os bytes do blob confirmado e grava esses bytes e um manifest SHA-256 imutáveis no SQLite. O manifest identifica run, metodologia, versão e hash, definição, commit base, commit validado, caminho e hash do conteúdo. A aprovação exige digest fornecido pelo aprovador local e confere o snapshot contra a versão publicada, o commit, o HEAD, os bytes atuais e a limpeza do workspace. O registro da aprovação persiste digest, definição e versão metodológica na mesma transação da decisão. Antes de `COMPLETED`, o motor confere novamente esse vínculo e falha se divergir. O Documentation Worker inclui hashes e commit do snapshot no relatório.

**Limite:** o artefato autorizado é o snapshot persistido do commit validado; o workspace mutável não é promovido nem mesclado automaticamente. Um processo que não respeite locks pode alterar o workspace após a última conferência, inclusive depois de `COMPLETED`; consumidores devem usar o commit e o digest aprovados e verificar novamente os bytes antes de qualquer promoção. Locks locais não são sandbox nem prova contra escrita hostil. A identidade do aprovador é local. Publicação da candidata exige decisão humana separada.

## ADR-036: AEC-RES-002 — quarentena após perda do manager

**Estado:** contenção implementada e regressão real final ensaiada em 2026-10-20. Supervisão completa da árvore de processos permanece pendente.

**Contexto:** um ensaio em diretório descartável encerrou o manager com `SIGKILL`. O Codex real continuou ativo em sua sessão própria. A recuperação antiga marcava a execução como `INTERRUPTED`, mas o lock do workspace desaparecia com o manager e outra execução podia começar.

**Decisão:** `INTERRUPTED` sem reconciliação mantém o workspace em quarentena. A admissão consulta execuções `STARTING`, `RUNNING` e `INTERRUPTED` em caminhos iguais ou sobrepostos, dentro de um lock local comum, antes de criar outro turno. O adapter informa PID ao manager logo após iniciar o processo, por um callback vinculado ao pedido de execução. O repositório grava PID, grupo, boot e instante de início do processo quando o Linux fornece esses dados. O boot ID distingue reinicializações; no mesmo boot, um grupo presente mantém o bloqueio mesmo se o PID tiver sido reutilizado, pois descendentes antigos podem existir. O instante de início auxilia a inspeção do operador. Se o grupo continuar ativo após a saída do processo principal, o turno termina `INTERRUPTED`. A liberação usa uma lista local de operadores e grava ator, evidência, avaliação dos efeitos externos (`VERIFIED`, `REVERTED` ou `UNKNOWN`) e hora em uma linha imutável; não altera o estado histórico `INTERRUPTED`. Um grupo associado ainda presente impede a liberação. O operador deve confirmar explicitamente a inspeção dos processos e do workspace mesmo quando o grupo original desapareceu. Nenhum turno é repetido automaticamente.

**Limite:** o AEC não envia sinais a um grupo após reinício. PID e PGID podem ser reutilizados, e um descendente pode sair do grupo. A conferência do grupo é conservadora, mas não prova que toda a árvore terminou. A liberação também depende da inspeção local real do operador e não autentica identidade remota. Cgroups ou escopos systemd, com testes de confinamento, são necessários antes de encerrar automaticamente uma árvore desconhecida ou executar código não confiável.

## ADR-037: AEC-GOV-003 — revisão completa e publicação governada de metodologias

**Estado:** implementado e testado com fixtures em 2026-10-20; `security-audit@1.0.3` publicada em 2026-10-23.

**Contexto:** o operador declarativo da ADR-014 acrescenta apenas um gate antes de QA. A candidata de auditoria muda agentes, permissões, transições e vínculo de integridade, e não possui passo QA. Publicá-la por `seed()` dispensaria avaliação e aprovação.

**Decisão:** a mesma proposta persistida aceita `METHODOLOGY_REVISION` com uma versão-base publicada, definição candidata completa e digest SHA-256 canônico. A avaliação valida schema e dependências, calcula o diff semântico, confere checks independentes, execução persistida da definição exata e digest das evidências. A decisão humana local fornece o digest da candidata; decisão e publicação reconferem as evidências. A publicação transacional verifica aprovação, avaliação, versão-base ativa, digest, diff e checks antes de inserir a nova versão imutável. Revisões não removem gates ou documentos obrigatórios nem aprovação humana. O bloqueio de `security-audit@1.0.2` continua.

**Efeito:** uma revisão completa pode seguir o processo supervisionado existente sem uma exceção exclusiva para auditoria. Alteração da candidata ou da evidência após avaliação invalida a decisão ou publicação. O teste de publicação usa banco e aprovador de fixture; ensaios Codex em outro banco não constituem proposta operacional. Identidade remota, sandbox e promoção do artefato auditado permanecem fora desta decisão.

## ADR-038: AEC-PUB-004 — registro verificável de evidência externa para publicação local

**Estado:** proposta `security-audit@1.0.3` preparada em 2026-10-20 e publicada em 2026-10-23 após autorização humana local.

**Contexto:** o ensaio Codex real da candidata usa um banco descartável no qual `1.0.3` é instalada apenas para testar sua execução. A proposta governada mantém `1.0.0` ativa e não deve copiar linhas SQLite manualmente nem usar esse bootstrap como publicação operacional.

**Decisão:** `ProposalStore.register_external_evidence()` registra uma referência imutável ao banco de origem, ao run, ao projeto Git e ao digest da evidência. Antes do registro, e novamente na avaliação, aprovação e publicação, a governança confere o hash da definição exata, o vínculo da versão, gates obrigatórios e, para auditoria com `audit_artifact`, manifest, conteúdo, blob Git, HEAD e workspace limpo. O Documentation Worker registra RFC e avaliação na persistência local da proposta. A ausência ou alteração da origem invalida o vínculo; somente uma decisão humana local explícita pode autorizar o digest da candidata.

**Limite:** não há transação atômica entre os dois bancos e o Git. Cada etapa revalida a origem, mas ela precisa permanecer acessível; a referência não arquiva uma cópia autocontida do ensaio. O teste está restrito à fixture local confiável, sem sandbox de sistema operacional. A proposta ficou em `AWAITING_APPROVAL` e `1.0.0` permaneceu ativa; `1.0.2` continua bloqueada.

## ADR-039: Medição de recursos restrita à Arena

**Estado:** `implementation-arena@1.0.1` testada com fixtures e Codex real, aprovada e publicada em 2026-10-23.

**Contexto:** `implementation-arena@1.0.0` não desempata duas soluções que passam nos mesmos gates. Tempo e memória podem fornecer evidência objetiva, mas uma única amostra ou um comando de teste diferente por candidato produziria uma comparação enganosa.

**Decisão:** a versão candidata exige um comando de carga previamente definido, idêntico nos dois snapshots. O controlador alterna cinco amostras e registra tempo decorrido, pico RSS e hash da saída. O desempate só ocorre com saída idêntica e intervalos de amostras sem sobreposição, com vantagem simultânea em tempo e memória. O QA recebe os resultados e examina a complexidade. Falhas de medição, saída variável e vantagens conflitantes não produzem vencedor. A versão publicada `1.0.0` mantém a regra anterior. A execução é limitada a projetos locais confiáveis em Linux com GNU `time`.

**Efeito:** a evidência de recursos fica associada à comparação imutável e ao relatório, sem alterar gates de outras metodologias. A carga, os dados e o limite de tempo devem ser escolhidos antes da execução; a medição não fornece sandbox nem uma garantia geral de complexidade assintótica. Publicação exige revisão governada e aprovação humana.

## ADR-040: Publicação local das revisões da Fase 7

**Estado:** adotada em 2026-10-23 para o escopo local supervisionado.

**Contexto:** Arena, TDD, auditoria e decisão haviam sido ensaiadas com Codex real em bancos descartáveis. A governança precisava vincular cada proposta operacional à definição exata e às evidências específicas de seu workflow. A Arena `1.0.1` também precisava ser reconhecida pelo Router depois da publicação.

**Decisão:** o digest de evidência externa passa a incluir, quando presentes, a comparação da Arena, snapshots TDD, ADR e entrada persistida do workflow. A revisão completa mantém digest canônico da candidata, diff semântico, checks independentes e publicação transacional. O usuário autorizou explicitamente a publicação; a identidade local autorizada registrou decisões vinculadas aos digests e publicou `implementation-arena@1.0.1`, `tdd-adversarial@1.0.1`, `security-audit@1.0.3` e `decision-adversarial@1.0.1` no Registry operacional. O Router admite o controlador sequencial da Arena `1.0.1` somente com comando de benchmark declarado e GNU `time` disponível em Linux. `security-audit@1.0.2` continua bloqueada.

**Limites:** a aprovação metodológica não aprova os runs de evidência nem mescla seus artefatos. A comparação real da Arena demonstrou empate e seleção por falha controlada de gate; não demonstrou superioridade natural de desempenho. Os bancos e projetos de origem permanecem referências locais necessárias para reconferência. Não há sandbox de sistema operacional, autenticação humana remota ou garantia sobre descendentes fora do grupo de processos. A Fase 8 não foi iniciada.

## ADR-041: Supervisão local por scope e identidade do operador no socket

**Estado:** implementada como capacidade opcional em 2026-10-23; implantação sob conta de serviço separada pendente.

**Contexto:** AEC-RES-002 mantém `INTERRUPTED` e bloqueia o workspace após queda do manager, mas um filho pode chamar `setsid()` e sair do PGID observado. As decisões humanas locais recebem um nome de ator do chamador, sem prova da identidade de quem fez a chamada.

**Decisão:** no modo Linux explicitamente habilitado, o adapter Codex reserva no SQLite uma scope `systemd --user` de nome aleatório exclusivo por execução antes de lançar o processo. O repositório registra nome da unidade e boot ID imutáveis e consulta seu estado junto ao PGID na conclusão, cancelamento e reconciliação. Falha de consulta bloqueia a liberação; unidade vazia ainda exige reconciliação humana de efeitos externos. O modo portátil anterior permanece disponível. Para chamadas locais de aprovação, publicação e reconciliação, um gateway por socket Unix deriva o UID de `SO_PEERCRED`, aplica permissões por ação e repassa a decisão aos componentes existentes; o cliente não envia a identidade do ator.

**Evidência:** testes executaram Codex simulado com descendente fora do PGID, recuperaram uma execução após `SIGKILL` do manager e confirmaram que o workspace continuou em quarentena. Um turno Codex real somente leitura passou em scope. Testes de socket cobriram UID permitido, UID desconhecido, ação proibida e rejeição de ator fornecido pelo cliente. Os gates finais estão em `.audit/local-supervision-verification.txt`.

**Limites:** a scope não é sandbox de segurança e não reconstrói efeitos externos. O gateway só estabelece uma fronteira de identidade se o serviço, o operador e os processos agentes tiverem permissões separadas: agentes não podem usar o UID autorizado do operador nem acessar o socket, e clientes não podem escrever o SQLite ou chamar a API como a conta do serviço. A instalação atual não demonstra essa separação. Não há autenticação remota nem serviço instalado automaticamente. A Fase 8 permanece não iniciada.
