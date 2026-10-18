# Governança da metodologia

O código confiável fornece `local_approvers` a `MethodologyGovernance`. O worker não recebe esse objeto. `decide()` valida o ator, o estado e o veredito persistido. Ele registra a identidade, o horário e o hash da proposta em SQLite. O texto de uma mensagem ACP não satisfaz essa verificação.

`publish()` exige estado `APPROVED`, hash da proposta ainda válido, avaliação positiva, versão-base igual à versão ativa e dependências de validação registradas. Uma transação insere a nova versão, atualiza a referência ativa, muda o estado da proposta e grava o evento. A chave única impede sobrescrita. Repetir a publicação da mesma proposta devolve o registro original.

O Registry conserva todas as versões publicadas e seus hashes. `bind_run()` aceita apenas uma execução cujo hash da definição corresponde à versão escolhida. A execução antiga não migra quando a referência ativa muda.

O cenário de teste cria uma identidade humana apenas no próprio teste. Nenhuma aprovação ou publicação real ocorre ao executar a suíte. A autorização local não substitui autenticação para uso remoto.
