# Componentes e responsabilidades

Este documento descreve os contratos desejados. Nenhum componente abaixo tem implementação funcional na Fase 0.

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

O Orquestrador planeja e acompanha uma execução dentro das permissões do sistema. O Implementador produz código e correções em ambiente autorizado. O QA adversarial aponta falhas com evidências e não altera silenciosamente o objeto analisado. O Worker de Documentação registra decisões, relatórios e vínculos com evidências. O Worker de Metodologia compara execuções e escreve RFCs; só uma aprovação humana permite publicar nova versão. Esses papéis são contratos de responsabilidade, não classes nem processos implementados hoje.

## Dependências entre componentes

O ACP depende apenas de seus contratos e da persistência local. A Execution Layer implementará uma interface de executor; Codex CLI será o primeiro adapter, e Ollama poderá ser adicionado depois. O Methodology Engine usa ACP e Control Plane para coordenar passos, sem acoplar definições de workflow a um provedor. A Governance Layer e o Quality Gate devolvem decisões ao Control Plane por interfaces explícitas. A Fase 1 definirá as interfaces concretas necessárias ao ACP, sem criar módulos vazios para os demais componentes.
