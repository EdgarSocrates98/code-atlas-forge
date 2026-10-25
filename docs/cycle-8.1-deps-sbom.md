# Cycle 8.1 — Dependency Audit & SBOM Validation (lf-0154, Phase 11)

Data da auditoria: **2026-10-25** · Branch `cycle-8.1-ga-readiness`
Toolchain: pip-audit 2.10.1 · pip 26.2.2 · node v22.23.2 · npm 10.9.8 ·
Python 3.12.3

## Estado do mundo auditado

- **Angular migration (lf-0147): commits landed** — steps 1/4→4/4
  commitados até `eacf06a` (Angular 21 → 22.2.3) + `c9f5fce` (e2e
  evidence regenerada). O spec ainda está em `factory/specs/active/`
  (não arquivado), mas `explorer/package.json` e `package-lock.json`
  estão limpos na worktree — **não há bump em voo** no momento do
  audit. Versões Angular: **22.2.3**. Se qualquer bump adicional
  aterrissar antes do release, re-rodar
  `.venv/bin/python scripts/generate_sbom.py` + os audits (o freshness
  test pega drift automaticamente).
- `sbom.json` do ciclo 8 (1140 componentes, gerador inline lf-0138)
  substituído por artefato regenerado (564 componentes) — ver
  "Mudanças no gerador".

## 1. SBOM regenerado

Novo gerador `scripts/generate_sbom.py` (stdlib-only, determinístico —
UUID5 serial, sem timestamp, byte-identical re-run). Fontes:
`pyproject.toml` + `explorer/package-lock.json`.

| Escopo | Componentes |
|---|---:|
| `required` | 24 (3 pypi + 21 npm) |
| `optional` | 1 (`sqlite-vec`) |
| `excluded` (dev-only) | 539 (6 pypi + 533 npm únicos) |
| **total** | **564** |

Lock: 622 entradas não-raiz → 554 `name@version` únicos (68 dedup de
paths aninhados `node_modules/x/node_modules/y`). Queda 1140→564 vs.
ciclo 8 = toolchain Angular 22 mais enxuto + dedup que o gerador
inline não fazia. Escopos normalizados para o enum CycloneDX 1.5
(`required`/`optional`/`excluded`; era `dev-only`, não-padrão).

Melhorias vs. gerador do ciclo 8: script versionado (era um bloco
inline referenciado por "ver git log"), campo `version` correto (spec
do dep não vaza mais como versão, ex. `"version": "pytest>=8.0"`),
licenças emitidas (npm via lock, PyPI via `importlib.metadata`),
serial UUID5 válido, modo `--check` para CI, dedup npm.

Gate: **`pytest tests/packaging/test_sbom_freshness.py` — 6/6 PASS**

```
test_is_cyclonedx_15_with_unique_refs PASSED
test_prod_deps_covered_exactly PASSED        # BOM = fontes, exato
test_sbom_matches_regeneration PASSED        # byte-identical
test_prod_licenses_all_permissive PASSED
test_optional_licenses_permissive_when_present PASSED
test_ecosystems_covered PASSED               # dependabot pip+gha+npm
```

## 2. License gate — conjunto prod

Classificação re-feita sobre o lock atual + metadata PyPI instalada.
**24/24 componentes `required` com licença permissiva; zero copyleft
novo.**

| purl | licença |
|---|---|
| `@angular/common`, `compiler`, `core`, `forms`, `platform-browser`, `router` @22.2.3 | MIT |
| `@standard-schema/spec@1.1.0`, `events@3.3.0`, `graphology@0.25.4`, `graphology-layout@0.6.1`, `graphology-layout-forceatlas2@0.10.1`, `graphology-types@0.24.8`, `graphology-utils@2.5.2`, `mnemonist@0.39.8`, `obliterator@2.0.5`, `pandemonium@2.4.1`, `sigma@3.0.2`, `zod@4.6.6`, `zone.js@0.15.1` | MIT |
| `rxjs@7.8.1` | Apache-2.0 |
| `tslib@2.6.3` | 0BSD |
| `tree-sitter@0.25.2`, `tree-sitter-python@0.23.6`, `tree-sitter-typescript@0.23.2` | MIT |

Opcional: `sqlite-vec` não instalado na venv → sem licença no BOM;
verificada manualmente via PyPI API (2026-10-25): `License: MIT
License, Apache License, Version 2.0` → permissiva. Gate permite
opcional sem licença registrada, mas **falha se registrada não-permissiva**.

## 3. Vulnerability audit — verbatim

### Python prod (requirements pinados)

```
$ .venv/bin/pip-audit -r /tmp/prod-reqs-lf0154.txt
# tree-sitter==0.25.2, tree-sitter-python==0.23.6, tree-sitter-typescript==0.23.2
No known vulnerabilities found
```

### Python env completo (dev incluído)

Primeira passada encontrou 12 CVEs no `pip 24.0` **da própria venv**
(não é dependência shipada — toolchain de dev):

```
Found 12 known vulnerabilities in 1 package
Name Version ID              Fix Versions
---- ------- --------------- ------------
pip  24.0    PYSEC-2026-1795 25.3
pip  24.0    PYSEC-2026-1796 26.0
pip  24.0    PYSEC-2026-2875 26.1
pip  24.0    PYSEC-2026-2876 26.1
pip  24.0    PYSEC-2026-196  26.1.2
pip  24.0    PYSEC-2026-1795 25.3
pip  24.0    PYSEC-2026-1796 26.0
pip  24.0    PYSEC-2026-196  26.1.2
pip  24.0    PYSEC-2026-2875 26.1
pip  24.0    PYSEC-2026-2876 26.1
pip  24.0    PYSEC-2026-3721 26.2
pip  24.0    PYSEC-2026-3721 26.2.0
```

Resolvido durante o audit: `pip install --upgrade pip` → 26.2.2, re-run:

```
$ .venv/bin/pip-audit
No known vulnerabilities found
Name             Skip Reason
---------------- -------------------------------------------------------------------------------
code-atlas-forge Dependency not found on PyPI and could not be audited: code-atlas-forge (0.1.0)
```

(`code-atlas-forge` skipped = pacote local, não publicado — esperado.)

### npm prod

```
$ cd explorer && npm audit --omit=dev
found 0 vulnerabilities
```

```json
"vulnerabilities": { "info": 0, "low": 0, "moderate": 0, "high": 0,
                     "critical": 0, "total": 0 },
"dependencies": { "prod": 22, "dev": 601, "optional": 166,
                  "peer": 25, "peerOptional": 0, "total": 622 }
```

(npm conta 22 "prod" vs. 21 entradas não-dev do lock — a contagem do
npm inclui o pacote raiz; sem divergência real.)

**Resultado: zero vulnerabilidades conhecidas no conjunto prod dos
dois ecossistemas. Nenhuma vuln prod unresolved → nenhum bloqueio de
GA por este gate.**

## 4. Dependabot

`.github/dependabot.yml` cobre os três ecossistemas pinados (npm fixed
no ciclo 8 — confirmado presente):

| ecosystem | directory | interval |
|---|---|---|
| pip | `/` | weekly |
| github-actions | `/` | weekly |
| npm | `/explorer` | weekly |

Cobertura travada por `TestDependabotCoverage` no teste de freshness.

## Mudanças neste spec

| arquivo | mudança |
|---|---|
| `scripts/generate_sbom.py` | **novo** — gerador CycloneDX 1.5 stdlib, determinístico, com licenças e `--check` |
| `sbom.json` | regenerado: 1140→564 componentes, Angular 22.2.3, escopos spec-válidos, licenças |
| `tests/packaging/test_sbom_freshness.py` | **novo** — 6 testes: validade, freshness byte-exata, cobertura prod exata, license gate, dependabot |
| `docs/SBOM.md` | reescrito: novo gerador, contagens, decisões |
| `.venv` | pip 24.0→26.2.2 (resolveu 12 CVEs do toolchain de dev) |
| `factory/specs/.../lf-0154` | inbox → active |

Não modificado (fora de escopo): `explorer/package.json`,
`explorer/package-lock.json` (owned pela migração lf-0147).

## NOT RUN / limitações declaradas

- Deps transitivas **PyPI fora do BOM** — não há `requirements.lock`;
  o audit pip-audit cobre o env instalado (superset), mas o SBOM só
  lista os pins diretos. Nível de verdade disponível, documentado em
  `docs/SBOM.md`.
- `sqlite-vec` licença verificada via PyPI API, não via artefato
  instalado (opcional, ausente da venv mínima).
- `npm audit` cobre apenas o registry advisory do npm; dependabot e
  audits futuros continuam responsáveis por drift pós-GA.
- Se lf-0147 fizer novos bumps antes do release: re-rodar gerador +
  audits — o freshness test torna o drift um failure explícito.
