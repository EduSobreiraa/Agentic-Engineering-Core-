# Segurança ACP v0.1

O broker emite um handle aleatório ao registrar cada agente. Em `publish`, ele compara o handle recebido à identidade registrada em memória, confirma que `sender` corresponde ao agente e consulta a tabela de permissões para tipo e destinatário. `claim`, `ack` e `nack` verificam a identidade do consumidor. Publicações rejeitadas por papel, remetente ou rota geram `PUBLISH_REJECTED`. Handles inválidos não oferecem identidade confiável para atribuir um evento.

A política completa está em [tipos de mensagens](MESSAGE_TYPES.md). Só o orquestrador atribui tarefas, solicita correções e publica conclusão. QA relata findings e validações. O worker de metodologia apenas propõe. A documentação só pode submeter artefatos ao orquestrador. O quality gate simulado envia resultados de validação. O broker não executa texto de mensagens.

Referências de artefatos aceitam apenas URI `artifact://` sem segmentos `.` ou `..`, inclusive codificados. Nenhum arquivo é aberto pelo ACP. Um resolvedor futuro precisará fixar uma raiz autorizada, resolver symlinks e verificar hash antes da leitura. O campo `sha256` nesta versão valida apenas 64 caracteres hexadecimais minúsculos.

Os handles protegem contra erros de associação entre objetos Python. Eles não são autenticação segura entre máquinas nem isolam processos locais maliciosos. Quem controla o processo Python ou o arquivo SQLite pode ler dados ou contornar o broker. `get_message` e `get_history` são operações locais para código confiável e não aplicam autorização por leitor. Não há transporte em rede, criptografia, expiração de handle, segredo persistido ou isolamento de tenants nesta versão.
