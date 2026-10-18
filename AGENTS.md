# Instruções para trabalhar no AEC

## Escopo e ordem

Leia `README.md`, `docs/architecture/ARCHITECTURE.md`, `docs/architecture/DECISIONS.md` e `docs/ROADMAP.md` antes de mudar contratos ou limites entre componentes. Siga as dependências do roadmap. Na Fase 0, mantenha o pacote sem lógica de orquestração.

## Código

- Use Python 3.11 ou mais recente, layout `src/aec` e anotações de tipo em funções novas.
- Mantenha o ACP independente de provedores de modelos, executores e metodologias.
- Coloque políticas e autorização em código determinístico. Agentes não concedem permissões a si mesmos.
- Prefira a biblioteca padrão. Acrescente dependências quando houver uso concreto e registre a razão.
- Não leia, registre, copie ou altere credenciais e sessões do Codex. Não exija `OPENAI_API_KEY`.
- Preserve arquivos e configurações existentes. Peça aprovação humana antes de operações destrutivas, merge, publicação ou mudança de metodologia.

## Verificação e documentação

- Teste comportamento observável e casos de falha para lógica nova. Use `tests/` e `pytest`.
- Execute `uv run ruff check .`, `uv run mypy` e `uv run pytest` ao concluir uma mudança de código.
- Atualize o roadmap quando uma tarefa for concluída. Registre decisões que mudem contratos ou governança em `docs/architecture/DECISIONS.md`.
- Documente evidências e limites da verificação. Não trate uma declaração de agente como aprovação de um quality gate.

O Orca é um ambiente de desenvolvimento e supervisão opcional. O núcleo deve funcionar fora dele. Skills do pstack podem informar metodologias, mas não são dependência de execução.
