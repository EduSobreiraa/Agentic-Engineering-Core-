# Agentic Engineering Core

O Agentic Engineering Core (AEC) é um projeto Python para coordenar trabalho de engenharia entre agentes especializados, verificações determinísticas e aprovação humana. O repositório está na Fase 0: contém a fundação do pacote e as decisões de arquitetura. Ainda não executa agentes nem troca mensagens ACP.

## Começar

Use Python 3.11 ou mais recente. No Fedora, com `uv` instalado:

```bash
uv sync --extra dev
uv run pytest
uv run ruff check .
uv run mypy
```

O ambiente de desenvolvimento usa ferramentas locais e não exige `OPENAI_API_KEY`. A integração planejada com Codex CLI usará a autenticação já disponível no computador, após validação na Fase 2.

## Estrutura

```text
src/aec/                  pacote Python
tests/                    verificações do pacote
docs/architecture/        arquitetura, componentes e decisões
docs/ROADMAP.md           tarefas e critérios por fase
```

O [roadmap](docs/ROADMAP.md) registra o progresso. A [arquitetura](docs/architecture/ARCHITECTURE.md) define limites e invariantes; [componentes](docs/architecture/COMPONENTS.md) distribui responsabilidades; [decisões](docs/architecture/DECISIONS.md) registra escolhas aceitas e pontos ainda abertos. As [instruções de contribuição](AGENTS.md) valem também para agentes que trabalham neste repositório.

## Próximo incremento

A Fase 1 especificará o ACP v0.1 e implementará modelos, autorização, broker local e persistência SQLite. O escopo atual não inclui executores, workflows ou agentes operacionais.
