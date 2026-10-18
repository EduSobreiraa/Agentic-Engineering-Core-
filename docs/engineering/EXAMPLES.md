# Cenário simulado

Execute `uv run pytest -q tests/scenarios/test_adversarial_loop.py`. O teste cria um repositório temporário com `calc.py` e testes de divisão. Na primeira iteração, o Implementador simulado omite tratamento explícito de zero; o QA emite um finding `HIGH` reproduzido. O ACP registra `REWORK_REQUEST`. Na segunda iteração, o Implementador corrige o código, o QA revisa o novo commit e `unittest` passa. O estado termina em `WAITING_APPROVAL`. Outro cenário mantém o defeito e termina `BLOCKED` após duas iterações.

Os testes também cobrem QA que altera o snapshot e um gate reprovado mesmo com QA positivo. Nenhum desses cenários chama o Codex real, faz merge ou modifica o projeto principal.

Para um experimento real isolado, execute explicitamente `uv run python -m aec.engineering.demo --real-codex`. O comando cria um repositório temporário novo, usa no máximo duas iterações e imprime o diretório que preserva banco, sessões e artefatos. Consome a franquia Codex da assinatura. O experimento não foi executado durante a implementação e não faz merge, push ou aprovação automática.
