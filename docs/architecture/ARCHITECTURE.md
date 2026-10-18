# Arquitetura do AEC

## Objetivo e estado

O AEC coordenará tarefas de engenharia por meio de agentes, artefatos verificáveis e decisões humanas. Este documento descreve a arquitetura alvo. Na Fase 0, apenas o pacote vazio e as ferramentas de desenvolvimento existem; nenhuma interação entre componentes está implementada.

## Fluxo previsto

```text
Pedido humano
    │
    ▼
Control Plane ─────► Governance Layer
    │                     │ autorização e aprovação
    ├────► Methodology Engine
    │          │ definição e versão fixada por execução
    ├────► ACP ◄──────────┐
    │       │ mensagens    │ resultados
    │       ▼              │
    └────► Execution Layer ┘
             │
             └────► Codex CLI, depois outros executores

Persistence Layer: tarefas, mensagens, estados, aprovações e referências a evidências
Quality Gate: verificações determinísticas acionadas pelo Control Plane
```

O Control Plane recebe a tarefa, identifica o escopo, escolhe uma metodologia publicada e fixa sua versão para a execução. Ele despacha trabalho pelo ACP e acompanha resultados da Execution Layer. A Governance Layer decide permissões e aprovações antes de ações críticas. O Quality Gate avalia resultados objetivos e devolve evidências ao Control Plane. A Persistence Layer guarda o estado necessário para rastrear e retomar a execução.

## Metodologias previstas

O primeiro fluxo completo será o ciclo Implementador, QA adversarial, correção, validação e aprovação. O desenho também contempla TDD, Implementation Arena, auditoria Red Team e Blue Team, decisão adversarial e melhoria por PDCA. A referência a GANs descreve a alternância entre produção e avaliação de artefatos; o AEC não treina redes neurais nem altera pesos de modelos. As demais metodologias entram somente nas fases indicadas no roadmap.

## Limites e invariantes

- O ACP descreve mensagens, identidade, correlação, idempotência e referências a artefatos. Ele não conhece Codex, Ollama nem passos de um workflow.
- A Execution Layer traduz pedidos autorizados em processos de um executor. Ela não escolhe metodologia nem concede permissões.
- O Control Plane não considera uma declaração de agente como prova de teste, autorização ou aprovação humana.
- Uma execução usa uma versão imutável de metodologia. Uma proposta de mudança não altera execuções em curso.
- O Worker de Metodologia só produz propostas e RFCs. Uma pessoa aprova a publicação de cada nova versão.
- Falhas, reinícios e repetição de mensagens não podem criar uma segunda ação crítica. A Fase 1 definirá as garantias exatas de entrega, ACK e deduplicação.
- Evidências e decisões devem ser ligadas à tarefa e à execução que as produziram.

## Persistência e recuperação previstas

SQLite será a fonte inicial de verdade para tarefas, mensagens, estados e aprovações. Diretórios de artefatos guardarão arquivos de evidência; o banco guardará suas referências. Git versionará código e definições de metodologia. JSONL poderá servir para exportar eventos, caso haja necessidade concreta; não será uma segunda fonte de verdade.

Na Fase 1, o desenho transacional do broker deverá explicitar quando uma mensagem entra na fila, quando um consumidor a confirma, e como o processamento repetido é reconhecido após reinício. Transições de estado e ações externas deverão ter autorização verificável e chaves de idempotência. Os contratos e a política de retenção ainda dependem da especificação ACP v0.1.

## Ordem de construção

O [roadmap](../ROADMAP.md) começa pelo ACP persistente, depois integra o Codex CLI, workflows e o ciclo adversarial. A [distribuição de responsabilidades](COMPONENTS.md) detalha cada componente. As [decisões](DECISIONS.md) registram escolhas e pendências.
