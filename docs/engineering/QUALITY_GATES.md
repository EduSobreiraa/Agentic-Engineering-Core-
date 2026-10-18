# Gates determinísticos

`CommandGate` registra previamente um identificador, vetor de argumentos e timeout. Comandos não são extraídos de respostas dos agentes. Todos os gates registrados são obrigatórios. `run_gates` verifica commit e ausência de alterações, inclusive arquivos ignorados pelo Git, antes e depois da execução; registra código de saída, estado, horário e saída limitada a 4 KiB. Saída não zero é `FAILED`; erro de execução ou timeout é `ERROR`. Qualquer resultado diferente de `PASSED` bloqueia aprovação.

O chamador deve registrar apenas comandos confiáveis do projeto. Os comandos rodam no snapshot QA, herdam o ambiente local para encontrar ferramentas instaladas e desativam escrita de bytecode Python. O mecanismo local não é sandbox de segurança para comandos hostis. Os resultados são persistidos em SQLite e enviados ao ACP como `VALIDATION_RESULT`.
