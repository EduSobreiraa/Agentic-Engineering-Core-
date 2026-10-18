# Pacotes de contexto

Os pacotes são JSON no prompt autorizado pelo workflow. O pacote do Implementador contém tarefa, critérios, paths, commit base, iteração e o relatório QA anterior. O pacote do QA contém tarefa, critérios, commit, iteração e diff. Conversas internas do Implementador não entram no pacote QA.

Cada workflow persiste `dev-adversarial@1.0.0`, identificador da tarefa, iteração e identificador da execução de engenharia em metadados. Um diff acima de 40 KiB bloqueia a revisão para evitar truncamento silencioso. Os pacotes são dados; permissões vêm do manager e da configuração local.
