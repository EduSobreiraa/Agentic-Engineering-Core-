# Exemplo declarativo

[example.yaml](example.yaml) descreve quatro etapas: plano, artefato, validação determinística e aprovação humana. A especificação pede uma função Python `add(a: int, b: int) -> int`. O YAML nomeia o executor e o validador, mas o código que executa o workflow deve registrá-los.

## Executar com adapter simulado

```bash
uv run python -m aec.workflows.demo --approve
```

O comando usa `SimulatedAdapter`, grava o histórico em `/tmp/aec-workflow.sqlite` e imprime `status=COMPLETED`. Sem `--approve`, para em `WAITING_APPROVAL`. Os [testes](../../tests/workflows/test_engine.py) exercitam recuperação, rejeição, concorrência, timeout e falhas.

## Executar com Codex CLI

```bash
uv run python -m aec.workflows.demo --real-codex --database /tmp/aec-phase3-real.sqlite --workspace /tmp/aec-phase3-real
```

`--real-codex` inicia dois turnos read-only e consome capacidade da assinatura. O comando não decide a aprovação humana. O validador `artifact-check` analisa a árvore sintática do texto produzido: exige uma única função `add` com parâmetros e retorno `int`, sem decoradores, defaults ou outras instruções, cujo corpo retorna `a + b`. Ele não executa o código nem prova comportamento além dessa estrutura.

Em 2026-10-18, o comando real retornou `WAITING_APPROVAL` para o run `5df4cc7c-48cd-4d4b-90c5-5e5db4a0e0d3`. As etapas `analyze`, `implement` e `validate` terminaram com sucesso; `validate` registrou `passed=True`. O ACP guardou duas atribuições, duas submissões e um `VALIDATION_RESULT`. O pedido de aprovação permaneceu `PENDING`. O texto do artefato foi `def add(a: int, b: int) -> int: return a + b`, com quebra de linha antes do `return`. A checagem sintática atual também aprovou esse texto ao reler o resultado persistido.

Uma execução anterior identificou um erro no exemplo: o prompt não continha a especificação e o validador aceitava qualquer resposta não vazia. O exemplo e o validador foram corrigidos antes do run acima. A execução anterior ficou sem aprovação.
