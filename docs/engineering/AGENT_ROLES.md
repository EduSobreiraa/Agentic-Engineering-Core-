# Papéis

O Implementador recebe especificação, critérios, paths permitidos, commit base e feedback anterior. O manager só concede `workspace-write` quando o agente está na lista local de escrita e a atribuição do orquestrador declara essa concessão. O núcleo captura o commit; a fala do agente não vale como prova de alteração.

O QA recebe especificação, critérios, commit revisado e diff. Ele executa em uma worktree separada com pedido `read-only`. O controlador rejeita alterações detectadas no snapshot. A worktree e o sandbox do executor não são uma barreira de segurança contra processos locais maliciosos; isolamento forte exige mecanismo do sistema operacional.

O orquestrador emite correções e aprovação. O quality gate publica resultados determinísticos. Nenhum agente pode alterar a política ou decidir a aprovação humana.
