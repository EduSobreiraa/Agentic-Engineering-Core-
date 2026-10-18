# RFC-METHOD-001

Este exemplo usa as fixtures `run-021` e `run-025` de `tests/scenarios/test_methodology_evolution.py`. Não é uma proposta de produção nem uma aprovação real.

## Problema

As duas execuções registram findings de tipagem na revisão de QA.

## Hipótese

Executar `type_check` antes de QA pode reduzir `average_review_iterations`.

## Mudança

Adicionar o gate declarativo `type_check` antes do passo `qa` em `dev-adversarial@1.1.0`. A versão-base é `dev-adversarial@1.0.0`.

## Evidências

- `run-021`.
- `run-025`.

## Avaliação

As fixtures comparativas registram 3 e 2 revisões na base, contra 2 e 1 na candidata. A média cai de 2,5 para 1,5. O avaliador retorna `PASS` para esse conjunto controlado. O teste não estabelece causalidade nem mede o executor real.

## Riscos e reversão

O gate pode atrasar revisões ou rejeitar trabalho válido. Para novas execuções, selecionar `dev-adversarial@1.0.0` reverte a escolha. Execuções existentes mantêm a versão fixada.

## Decisão

O teste usa `human-test` como identidade autorizada local e publica 1.1.0. Fora do teste, a decisão permanece sujeita à aprovação humana explícita.
