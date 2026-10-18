# Worktrees e snapshots

O AEC cria a worktree do Implementador fora do repositório principal a partir de um commit real. Depois da execução, rejeita alterações fora dos paths permitidos, symlinks e remoções, cria um commit local e registra seu hash. Cada revisão usa uma worktree QA separada no commit capturado. O AEC não remove worktrees automaticamente e nunca usa `reset --hard`, merge ou push.

O pedido QA usa o modo `read-only` do adapter e o controlador verifica alterações locais antes e depois da revisão. Git worktrees compartilham o repositório e não isolam permissões do sistema operacional. Para agentes não confiáveis, acrescente isolamento do sistema operacional antes de executar código.
