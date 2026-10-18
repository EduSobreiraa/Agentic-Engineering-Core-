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

O ambiente de desenvolvimento usa ferramentas locais e não exige `OPENAI_API_KEY`. A integração planejada com Codex CLI usará a autenticação já disponível no computador, após validação na Fase 2.

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

O ACP v0.1 não executa agentes LLM, processos externos nem workflows. Handles locais não oferecem autenticação segura entre máquinas. O SQLite guarda mensagens e recibos; efeitos externos futuros precisam de reconciliação própria.
