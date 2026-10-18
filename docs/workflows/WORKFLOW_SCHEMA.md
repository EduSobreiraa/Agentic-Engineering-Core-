# Esquema de workflows

O carregador aceita arquivo `.yaml` ou `.yml` UTF-8, usa `yaml.safe_load` e rejeita campos desconhecidos nos modelos. A versão deve seguir `1.x.y`; o hash SHA-256 é calculado sobre a representação JSON normalizada da definição validada, não sobre os bytes do arquivo. A definição é copiada para o SQLite no início e mudanças posteriores no YAML não a substituem.

| Campo | Regra |
| --- | --- |
| `id`, `version`, `name`, `steps` | Obrigatórios. IDs usam letras, números, `_` ou `-`; `steps` não pode ser vazio. |
| `limits` | `max_parallel_steps` padrão 1; `max_total_steps` padrão 100; `max_duration_seconds` padrão 1800. Todos devem ser positivos. |
| `steps[].type` | `agent`, `validation` ou `human_approval`. |
| `steps[].depends_on` | IDs existentes, distintos e sem ciclos. |
| `steps[].condition` | `{step: <dependência validation>, result_equals: <bool>}`. |
| `steps[].retry_policy` | `max_attempts` entre 1 e 10; para `agent` e `human_approval` deve ser 1. |
| `steps[].timeout_seconds` | Valor positivo. Sem ele, execução e validação usam 120 segundos. |
| `steps[].inputs` | Mapa de strings. `{{id.output}}` referencia somente uma dependência `agent` direta. |
| `steps[].outputs`, `metadata` | Dados declarativos preservados na definição; não acionam código. |

Uma etapa `agent` exige `agent`, `executor` e `inputs.prompt`. O agente e adapter devem estar registrados; os agentes de etapa têm papel `IMPLEMENTER`. Uma etapa `validation` exige o nome de uma função registrada em `validator`. Aprovação não aceita agente, executor ou validator. Não há comandos de shell, código Python ou expressões livres no YAML.

O modelo de execução contém `run_id`, ID e versão do workflow, hash, estado e horários. Cada etapa contém ID da execução, estado, contador de tentativas, ID da execução externa, horários e resultado. Veja um arquivo completo em [example.yaml](example.yaml).
