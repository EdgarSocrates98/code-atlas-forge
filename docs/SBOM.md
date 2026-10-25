# SBOM — Code Atlas Forge

`sbom.json` na raiz — **CycloneDX 1.5** mínimo, gerado por script stdlib
(`json` + `tomllib`, sem dependência nova) sobre as fontes pinadas:

- `pyproject.toml` → runtime `dependencies` (exact `==`), grupos
  `optional-dependencies` (`dev`, `vector`);
- `explorer/package-lock.json` → árvore npm travada (lockfileVersion 3,
  `packages[*].dev` separa runtime de dev-only).

## Contagem

| Escopo | Componentes |
|---|---:|
| `required` (runtime: 3 pypi + 21 npm prod/transitivos) | 24 |
| `optional` (`sqlite-vec`, grupo `vector`) | 1 |
| `dev-only` (toolchain py + npm dev) | 1115 |
| **total** | **1140** |

## Regeneração

```bash
.venv/bin/python - <<'EOF'
# o mesmo bloco stdlib usado no ciclo 8 — ver git log de sbom.json
EOF
```

## Decisões

- **Sem tool externa**: o ambiente não tem `cyclonedx-bom`/`syft`; o
  inventário stdlib sobre os dois arquivos de lock declara exatamente o
  que é pinado — honesto, reproduzível, sem dep nova (policy do repo:
  dependency-light).
- Deps transitivas **Python não são listadas** (não há `requirements.lock`
  — pip resolve em install-time). As transitivas **npm são listadas**
  porque `package-lock.json` as fixa — é o nível de verdade disponível.
- `code-atlas-forge` (o próprio pacote) figura só em `metadata.component`.
