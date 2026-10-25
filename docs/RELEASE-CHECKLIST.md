# Release Checklist — `1.0.0rc1` → GA

Gate reproduzível para cortar uma release do code-atlas-forge. Tudo que
é automático roda via **`scripts/release_check.sh`** (bateria completa,
não-interativa, sai não-zero em falha). Os passos manuais estão no fim.

Política de versão: `docs/RELEASING.md`.

## 0. Pré-requisitos (uma vez por máquina)

```bash
python3 -m venv .venv && source .venv/bin/activate
python -m pip install -e '.[dev]'          # ruff, mypy, pytest, build
python -m pip install 'pip-audit>=2.7'     # gate de deps Python (como o CI)
cd explorer && npm ci                      # Node 22 (ver .github/workflows/ci.yml)
npx playwright install chromium            # só para o gate E2E
```

Ferramentas esperadas no PATH/venv: `ruff`, `mypy`, `pytest`,
`python -m build`, `pip-audit`, `npm`, `npx`, `unzip` (ou `python -m
zipfile`), Chrome/Chromium headless para `ng test`
(`CHROME_BIN=/usr/bin/google-chrome` no CI).

## 1. Bateria automática (um comando)

```bash
scripts/release_check.sh
```

Flags para iteração parcial — **nunca usar no corte de release**:

```bash
scripts/release_check.sh --skip-explorer   # pula npm ci/build/test/audit
scripts/release_check.sh --skip-e2e        # pula Playwright (mantém unit)
scripts/release_check.sh --skip-audits     # pula npm audit + pip-audit
```

O script executa, nesta ordem, e falha fechado:

| # | gate | comando efetivo |
|---|---|---|
| 1 | versão consistente | `__version__` == seção mais recente do CHANGELOG (`## [X.Y.Z]`) |
| 2 | lint | `ruff check src tests` |
| 3 | tipos | `mypy src` |
| 4 | testes Python | `pytest -q` |
| 5 | build | `python -m build` → `dist/` |
| 6 | inventário do wheel | `unzip -l` confirma `code_atlas/{api,context,contracts,discovery,domains,eval,federation,graph,impact,indexing,observability,parsers,resolvers,search,storage}/` + versão no nome do arquivo |
| 7 | smoke de instalação | venv limpa + `pip install --no-deps` do wheel → `code-atlas --version` |
| 8 | smoke funcional | `code-atlas index tests/fixtures/sample_repo --json`, `status --json`, `doctor` (venv dev) |
| 9 | explorer deps | `npm ci` |
| 10 | explorer build | `npm run build` (produção) |
| 11 | explorer unit | `npm test -- --watch=false --browsers=ChromeHeadless` |
| 12 | explorer e2e | `npm run e2e` (Playwright real: indexa fixture + sobe `code-atlas explorer`) |
| 13 | auditoria npm | `npm audit --omit=dev --audit-level=critical` |
| 14 | auditoria pip | `pip-audit -r reqs.txt --strict` (freeze sem o próprio pacote, como o CI) |

Saída termina com tabela `PASS/FAIL/SKIP` por gate. Qualquer `FAIL`
→ exit 1. `SKIP` acontece só por flag explícita ou ferramenta
declaradamente ausente — e é impresso em destaque (NOT RUN declarado).

## 2. Verificações manuais (humano)

- [ ] `git status` limpo na branch de release; WIP commitado ou
      descartado conscientemente.
- [ ] `git log` desde o último corte revisado; CHANGELOG cobre o que o
      diff mostra (não só o que as specs dizem).
- [ ] `scripts/release_check.sh` **sem flags** verde na máquina de
      release.
- [ ] CI verde no commit exato do corte (`gh run list -b <branch>`).
- [ ] Achados P0/P1 de revisão externa: zero abertos (Blueprint §13).
- [ ] Docs de limitação atualizadas (`docs/cycle-*-limitations*.md`,
      `docs/RELEASING.md` § deferidos).
- [ ] `pyproject.toml`: classifier `Development Status` coerente
      (`4 - Beta` em RC, `5 - Production/Stable` no GA — ver
      `docs/RELEASING.md` § deferidos).
- [ ] `explorer/package.json` `version` alinhado por cortesia no GA.

## 3. Corte (humano — nunca automatizado)

```bash
git tag -a v1.0.0rc1 -m "code-atlas-forge 1.0.0rc1 — release candidate"
git push origin v1.0.0rc1
```

Publicação PyPI: **não configurada** (sem workflow de publish;
`docs/RELEASING.md` § fluxo). Se publicar manualmente:

```bash
python -m build
python -m twine check dist/*
# twine upload dist/code_atlas_forge-1.0.0rc1*   # conta/token do maintainer
```

## 4. Conhecidos/deferidos neste RC

- ~~`npm audit --omit=dev` reporta 8 advisories (4 high, 4 moderate) em
  `@angular/*@18.2.13`~~ — **resolvido em lf-0147 (cycle-8.1)**:
  `@angular/*@22.2.3`, `npm audit --omit=dev` = 0 vulnerabilities.
- Wheel pip não carrega o dist Angular — `code-atlas explorer`
  instalado via pip serve API-only com aviso honesto.
- Se a bateria achar regressão → bump `rc2` e repetir este checklist
  do zero.
