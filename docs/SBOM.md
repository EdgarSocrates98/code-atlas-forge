# SBOM — Code Atlas Forge

`sbom.json` na raiz — **CycloneDX 1.5**, gerado por
`scripts/generate_sbom.py` (stdlib-only: `json` + `tomllib` +
`importlib.metadata`, sem dependência nova) sobre as fontes pinadas:

- `pyproject.toml` → runtime `dependencies` (exact `==`) viram
  `scope: required`; grupos `optional-dependencies` viram `optional`
  (`vector`) ou `excluded` (`dev` — toolchain de desenvolvimento);
- `explorer/package-lock.json` → árvore npm travada (lockfileVersion 3,
  `packages[*].dev` separa runtime de dev-only). Entradas aninhadas com
  o mesmo `name@version` colapsam num único componente (CycloneDX
  dedup); se o mesmo artefato aparece em path prod *e* dev, `required`
  vence.

Escopos seguem o enum do spec 1.5: `required` / `optional` / `excluded`
(o ciclo 8 usava o não-padrão `dev-only`; normalizado no lf-0154).

## Contagem (regenerado 2026-10-25, Angular 22.2.3)

| Escopo | Componentes |
|---|---:|
| `required` (runtime: 3 pypi + 21 npm prod/transitivos) | 24 |
| `optional` (`sqlite-vec`, grupo `vector`) | 1 |
| `excluded` (dev-only: 6 pypi + 533 npm únicos) | 539 |
| **total** | **564** |

O lock tem 622 entradas `packages[*]` não-raiz → 554 `name@version`
únicos (68 duplicatas por paths aninhados). A queda de 1140 → 564 vs.
o SBOM do ciclo 8 reflete (a) o toolchain Angular 22 mais enxuto e
(b) a deduplicação que o gerador antigo não fazia.

## Regeneração

```bash
.venv/bin/python scripts/generate_sbom.py            # reescreve sbom.json
.venv/bin/python scripts/generate_sbom.py --check    # freshness gate (diff)
```

Rodar **dentro da venv**: licenças PyPI saem de `importlib.metadata` dos
dist instalados; fora da venv elas ficam ausentes e o license gate falha
honestamente. Licenças npm sempre saem do campo `license` do lock.

Gate automatizado: `pytest tests/packaging/test_sbom_freshness.py`
(cobertura exata de prod, licenças prod 100% permissivas, cobertura
Dependabot pip+gha+npm).

## Decisões

- **Sem tool externa**: o inventário stdlib sobre os dois arquivos de
  lock declara exatamente o que é pinado — honesto, reproduzível
  (serial UUID5 determinístico, sem timestamp; mesma entrada → mesmo
  bytes), sem dep nova (policy do repo: dependency-light).
- Deps transitivas **Python não são listadas** (não há
  `requirements.lock` — pip resolve em install-time). As transitivas
  **npm são listadas** porque `package-lock.json` as fixa — é o nível de
  verdade disponível.
- `code-atlas-forge` (o próprio pacote) figura só em
  `metadata.component`.
- `sqlite-vec` (opcional) não está instalado na venv → sem licença no
  BOM; verificada manualmente como MIT/Apache-2.0 via PyPI (lf-0154).
