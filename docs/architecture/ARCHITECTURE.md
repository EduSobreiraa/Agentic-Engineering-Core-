# Arquitetura do AEC

## Objetivo e estado

O AEC coordena tarefas de engenharia por meio de agentes, artefatos verificáveis e decisões humanas. A Fase 1 implementou o ACP local; a Fase 2 acrescentou um manager de execução e o adapter Codex CLI; a Fase 3 acrescentou um Workflow Engine local para grafos acíclicos; a Fase 4 implementou o ciclo adversarial simulado. A Fase 5 acrescentou um worker para relatórios documentais determinísticos. O Control Plane geral permanece planejado.

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
    ├────► Workflow Engine │
    │       │ DAG e estados │
    └────► Execution Layer ┘
             │
             └────► Codex CLI, depois outros executores

Persistence Layer: tarefas, mensagens, estados, aprovações e referências a evidências
Quality Gate: verificações determinísticas acionadas pelo Control Plane
```

O Control Plane recebe a tarefa, identifica o escopo, escolhe uma metodologia publicada e fixa sua versão para a execução. Ele despacha trabalho pelo ACP e acompanha resultados da Execution Layer. A Governance Layer decide permissões e aprovações antes de ações críticas. O Quality Gate avalia resultados objetivos e devolve evidências ao Control Plane. A Persistence Layer guarda o estado necessário para rastrear e retomar a execução.

## Metodologias previstas

O primeiro fluxo completo é `dev-adversarial@1.0.0`, com Implementador, QA adversarial, correção, validação e aprovação. O controlador especializado persiste iterações e reutiliza workflows acíclicos para cada agente e para a aprovação. O desenho também contempla TDD, Implementation Arena, auditoria Red Team e Blue Team, decisão adversarial e melhoria por PDCA. A referência a GANs descreve a alternância entre produção e avaliação de artefatos; o AEC não treina redes neurais nem altera pesos de modelos. As demais metodologias entram somente nas fases indicadas no roadmap.

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

O [ACP v0.1](../protocols/ACP_SPEC.md) define gravação antes da entrega, lease, ACK, recibos únicos e recuperação. A retenção atual é indefinida. Ações externas e aprovação humana permanecem fora do broker.

O [Workflow Engine](../workflows/WORKFLOW_ENGINE.md) persiste a definição e o hash por execução, coordena etapas acíclicas, registra aprovações locais e usa o ACP e o Execution Manager. Não autentica pessoas ou coordena managers em processos distintos. Resultado externo desconhecido interrompe o workflow sem repetição automática.

A [Execution Layer](../execution/EXECUTION_LAYER.md) grava turnos e sessões no mesmo SQLite. Ao reiniciar, marca turnos em andamento como `INTERRUPTED` somente se não houver lock de execução ativo, sem repetir efeitos externos. O manager usa um semáforo por processo e travas locais por execução e workspace. O scheduler de workflow ainda exige um único motor ativo por execução.

O [Worker de Documentação](../documentation-worker/ARCHITECTURE.md) lê registros tipados dessas camadas e grava relatórios históricos no mesmo SQLite, com um artefato Markdown e SHA-256. Um validador registrado permite que o Workflow Engine peça documentação pelo ACP. Revisões de ADR também ficam no SQLite, vinculadas à execução. A documentação técnica é uma proposta renderizada; o worker não altera projetos externos ou decisões humanas.

## Ordem de construção

O [roadmap](../ROADMAP.md) começa pelo ACP persistente, depois integra o Codex CLI, workflows e o ciclo adversarial. A [distribuição de responsabilidades](COMPONENTS.md) detalha cada componente. As [decisões](DECISIONS.md) registram escolhas e pendências.
