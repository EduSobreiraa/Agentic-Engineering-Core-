# Condições e validação

A condição implementada compara o campo booleano `passed` de uma etapa `validation` dependente com `result_equals`. A função registrada do validador recebe `inputs: dict[str, str]` e deve retornar `bool`; outro tipo causa falha da tentativa. Resultado ausente ou sem `passed` booleano impede a decisão. Condição falsa marca a etapa como `SKIPPED`.

O motor substitui `{{etapa.output}}` nos inputs usando a saída de uma dependência direta de tipo `agent`. Saída ausente ou incompatível gera erro, sem interpretação de linguagem natural. O YAML não contém funções nem expressões executáveis.

Uma validação que retorna `False` é uma avaliação concluída (`SUCCEEDED`, `passed: false`); use uma condição em etapas posteriores para impedir sua execução. Etapas ignoradas contam como resolvidas, mas uma aprovação humana ignorada impede a conclusão e marca o workflow `FAILED`.
