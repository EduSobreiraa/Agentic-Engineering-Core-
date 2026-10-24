# Revisão de `security-audit@1.0.3`

**Estado em 2026-10-23:** proposta local `AEC-PUB-004-SECURITY-AUDIT-103` avaliada `PASS_WITH_WARNINGS`, aprovada pela identidade local autorizada após autorização explícita do usuário e publicada no Registry operacional. A publicação metodológica não aprovou o run `real-static-audit`, que permanece em `WAITING_APPROVAL`.

## Artefato sob revisão

- Metodologia: `security-audit@1.0.3`.
- SHA-256 da definição normalizada: `d2ab1b0674f703d385beff5272a796f273644544cf7e6d83530af56dd0db39e5`.
- SHA-256 do contrato `MethodologyVersion` serializado: `23c4a633ff78ca52ddbc4936d0904d93afb640418e2afeae7354e6ba725478b9`.
- SHA-256 canônico do contrato completo para aprovação de revisão: `b5eb70c34789c7a6fb7cc432420601076d5a4447bde13bb1d4af15e79dc68cc8`.
- O run de auditoria fixa a versão publicada no Registry. O reteste grava commit, bytes e manifest imutáveis do único arquivo permitido. A aprovação do run exige o digest do manifest e a conclusão reconfere o vínculo. Nenhum merge ou promoção do workspace mutável ocorre automaticamente.

## Fluxo verificado

`authorize_scope → threat_model → static_analysis → red_review → blue_proposal → verify_evidence → blue_fix → retest → approval`.

O cenário E003 usa `unsafe.py` em projeto local descartável, com `eval(text)` intencional. Red Team produz finding ACP; Blue Team propõe e aplica a correção no mesmo run; o reteste inspeciona estaticamente o blob Git confirmado e registra snapshot. A execução do arquivo vulnerável e testes perigosos ficam fora do escopo.

Em 2026-10-20, após AEC-RES-002, `AEC_RUN_REAL_CODEX=1 timeout 480s uv run pytest -q tests/scenarios/test_phase7_workflows.py::test_real_static_audit_and_safe_fixture_correction` passou em 89,61 s. O teste verificou finding ACP, `retest` aprovado, bytes do snapshot iguais ao arquivo corrigido, integridade do digest, relatório documental e estado `WAITING_APPROVAL`. Não houve decisão humana.

Os cenários com fixtures cobrem correção ausente, escopo, adulteração antes e depois da aprovação, corrida antes da conclusão, reinício, snapshot imutável e bloqueio da versão `1.0.2`. A suíte padrão passou com 186 testes e 9 opt-in pulados; Ruff e mypy passaram. Evidência anterior do hotfix: `.audit/aec-sec-001-verification.txt`.

## Revisão governada preparada

AEC-GOV-003 aceita `METHODOLOGY_REVISION` para uma definição completa. A base é `security-audit@1.0.0`; a candidata é `1.0.3` com o digest canônico acima. O diff semântico registra o novo agente `blue_corrector` com permissão `workspace-write`, o novo gate obrigatório `audit_retest`, o passo `retest`, o vínculo de aprovação `audit_artifact`, a transição de aprovação agora dependente do reteste e o limite de nove passos. O Registry preserva o bloqueio de `1.0.2`.

O teste de governança submete, avalia, aprova com identidade local **simulada** e publica a definição em banco descartável. Ele também rejeita alterações da candidata ou da evidência após avaliação. Esse teste não constitui aprovação desta candidata.

AEC-PUB-004 repetiu o ensaio Codex real em um repositório descartável preservado. O teste passou em 87,07 s e deixou o workflow `real-static-audit` em `WAITING_APPROVAL`. Seu SQLite e projeto Git estão em `.audit/aec-pub-004/real-run/test_real_static_audit_and_saf0/`. A API `ProposalStore.register_external_evidence()` registrou a referência `aec-pub-004-real-audit` no banco local `.audit/aec-pub-004/operational.sqlite`, sem copiar linhas do banco de origem. O registro fixou o digest `8ec8d8efde543156f2338e030d0b98b59ce074c6835b516224077a9c1165058d` e confere a definição exata, o vínculo, os gates, o snapshot, o blob Git e o workspace antes de usar a evidência. Se a origem mudar ou desaparecer, a aprovação ou publicação falha.

A proposta foi submetida com digest canônico da candidata `b5eb70c34789c7a6fb7cc432420601076d5a4447bde13bb1d4af15e79dc68cc8`; sua avaliação vinculou o digest consolidado das evidências `140a86dd898d146498892c1c250d8350f1220a3f06dc0b0a4028adb649622d79`. O Documentation Worker gravou RFC, avaliação, decisão e publicação imutáveis no banco local. A avaliação incluiu checks do vínculo de aprovação, do reteste obrigatório e do run real, com limitações explícitas da fixture. O Registry local ativou `security-audit@1.0.3` pela API governada; o hash da linha publicada é `32943930baf934fdf908e7b65f506a13bb0bcde312c8db823b74077d8171150e`. `seed()` não substituiu esse procedimento operacional.

## Limites para a decisão humana

- A validação real terminou em `WAITING_APPROVAL`; aprovação e conclusão foram verificadas com fixtures.
- O banco de origem e o repositório Git local precisam permanecer disponíveis para novas conferências; a referência não é um arquivo de evidência autocontido.
- O validador prova apenas a propriedade estática específica da fixture. Não é auditoria de segurança geral.
- O snapshot e o blob Git são a referência validada. Uma promoção futura deve consumir esse commit e digest, não copiar o workspace mutável sem reconferência.
- Não há sandbox de sistema operacional, supervisão de descendentes fora do PGID nem identidade humana remota.
- `security-audit@1.0.2` continua bloqueada. A versão `1.0.3` foi publicada; nenhum código de run foi mesclado.
