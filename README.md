# Agentic Engineering Core

O Agentic Engineering Core (AEC) é um projeto Python para coordenar trabalho de engenharia entre agentes especializados, verificações determinísticas e aprovação humana. A Fase 1 implementa o ACP v0.1 para agentes simulados, com validação Pydantic, broker local e persistência SQLite.

## Começar

Use Python 3.11 ou mais recente. No Fedora, com `uv` instalado:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
uv run python -m aec.simulation.agents
```

O ambiente de desenvolvimento usa ferramentas locais e não exige `OPENAI_API_KEY`. A Fase 2 integra o executável Codex CLI e usa a autenticação já disponível. O teste padrão usa um executável falso e não consome a assinatura.

Para executar a demonstração real com dois agentes e persistir o histórico:

```bash
uv run python -m aec.execution.demo --database /tmp/aec-phase2.sqlite --workspace /tmp/aec-phase2-work
```

Esse comando inicia dois turnos Codex read-only e consome capacidade da assinatura. Consulte [execução](docs/execution/EXECUTION_LAYER.md), [contratos](docs/execution/EXECUTOR_CONTRACT.md) e [testes](docs/execution/TESTING.md).

## Estrutura

```text
src/aec/                  pacote Python
tests/                    verificações do pacote
docs/architecture/        arquitetura, componentes e decisões
docs/protocols/             contratos e limites do ACP v0.1
docs/ROADMAP.md           tarefas e critérios por fase
```

O [roadmap](docs/ROADMAP.md) registra o progresso. A [arquitetura](docs/architecture/ARCHITECTURE.md) define limites e invariantes; [componentes](docs/architecture/COMPONENTS.md) distribui responsabilidades; [decisões](docs/architecture/DECISIONS.md) registra escolhas aceitas. A [especificação ACP](docs/protocols/ACP_SPEC.md) descreve o contrato implementado. As [instruções de contribuição](AGENTS.md) valem também para agentes que trabalham neste repositório.

## Limites atuais

O ACP v0.1 permanece independente de executores. A Execution Layer inicia processos Codex e guarda estados, mas não executa workflows nem quality gates. Handles locais não oferecem autenticação segura entre máquinas. Reinícios interrompem turnos em andamento sem os repetir automaticamente.
