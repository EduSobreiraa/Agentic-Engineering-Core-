# Governança da metodologia

O código confiável fornece `local_approvers` a `MethodologyGovernance`. O worker não recebe esse objeto. `decide()` valida o ator, o estado e o veredito persistido. Ele registra a identidade, o horário e o hash da proposta em SQLite. O texto de uma mensagem ACP não satisfaz essa verificação.

`publish()` exige estado `APPROVED`, hash da proposta ainda válido, avaliação positiva, versão-base igual à versão ativa e dependências de validação registradas. Uma transação insere a nova versão, atualiza a referência ativa, muda o estado da proposta e grava o evento. A chave única impede sobrescrita. Repetir a publicação da mesma proposta devolve o registro original.

O Registry conserva todas as versões publicadas e seus hashes. `bind_run()` aceita apenas uma execução cujo hash da definição corresponde à versão escolhida. A execução antiga não migra quando a referência ativa muda.

O cenário de teste cria uma identidade humana apenas no próprio teste. Nenhuma aprovação ou publicação real ocorre ao executar a suíte. A autorização local não substitui autenticação para uso remoto.

## Requisitos para uma futura aprovação remota

Uma interface de rede deverá autenticar a pessoa por um mecanismo externo verificável, ligar a identidade autenticada a uma permissão específica de aprovação ou publicação e impedir que agentes forneçam ou alterem essa identidade. A decisão deverá referenciar o ID e o hash da proposta, o hash da avaliação, a ação, o instante e o ator autenticado. O servidor deverá verificar essas referências e a versão ativa em transação antes de publicar; repetição da mesma requisição precisa ser idempotente. Sessões expiradas, silêncio, timeout, mensagens ACP e strings no payload não constituem aprovação. A interface também deverá registrar recusas, revogação de acesso e trilha de auditoria protegida. A lista `local_approvers` não satisfaz esses requisitos fora do operador local supervisionado.
