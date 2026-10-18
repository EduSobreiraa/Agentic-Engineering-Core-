# Componentes e responsabilidades

Este documento descreve os limites dos componentes. ACP, Execution Layer, Workflow Engine, aprovação local e ciclo adversarial simulado possuem implementação; o Control Plane geral e a governança distribuída ainda são alvo futuro.

| Componente | Responsabilidade | Entrada e saída esperadas | Fora de sua autoridade |
| --- | --- | --- | --- |
| Control Plane | Classificar tarefas, escolher workflows, controlar estados e limites | Pedido, eventos e resultados; comandos autorizados e decisões de estado | Criar permissões ou aprovar a própria execução |
| ACP | Validar e transportar mensagens estruturadas, correlação, ACK e deduplicação | Envelope e payloads versionados; mensagens persistidas e confirmações | Escolher executor ou metodologia |
| Execution Layer | Executar pedidos em ambientes isolados e capturar resultados | Pedido autorizado; saída, estado e referências a artefatos | Alterar políticas ou a metodologia |
| Methodology Engine | Carregar definições declarativas, aplicar transições e fixar versão por execução | Definição publicada e estado; próximo passo permitido | Publicar a própria definição |
| Governance Layer | Aplicar políticas, registrar aprovações e bloquear ações críticas sem autorização | Identidade, ação, contexto e decisão humana; autorização verificável | Aceitar afirmação de agente como aprovação |
| Quality Gate | Rodar verificações objetivas e registrar evidências | Artefatos e critérios; resultado e evidência | Substituir julgamento humano onde ele é exigido |
| Persistence Layer | Persistir tarefas, mensagens, estados, aprovações e referências | Operações transacionais; dados recuperáveis | Decidir política ou interpretar conteúdo de agente |

## Papéis de agentes

O Orquestrador acompanha uma execução dentro das permissões do sistema. O Implementador produz código e correções em worktree autorizada. O QA adversarial aponta falhas com evidências em snapshot separado. Esses três papéis são usados pelo ciclo da Fase 4. O Worker de Documentação e o Worker de Metodologia permanecem planejados; só uma aprovação humana permite publicar nova metodologia.

## Dependências entre componentes

O ACP depende apenas de seus contratos e da persistência local. A Execution Layer implementa uma interface de executor com Codex CLI como primeiro adapter. O Workflow Engine coordena DAGs; `AdversarialLoop` persiste iterações e reutiliza seus passos. A aprovação humana usa a infraestrutura local do Workflow Engine. Os gates da Fase 4 usam comandos registrados pelo projeto. Ollama e seleção geral de metodologias permanecem futuras.
