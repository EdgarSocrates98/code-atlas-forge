# Real-world benchmark (Cycle 8 — lf-0128)

Mede o pipeline `code-atlas` contra **repositórios OSS reais** (não corpus
sintético): tempo de `index` (wall + `stage_ms_*`), contagens do grafo,
latência dos endpoints `/api/v1` num servidor real, e o delta de custo dos
adapters de domínio (`--domains all`).

## Corpus

`manifest.json` — 5 repos shallow-cloned, SHA-pinado, formas distintas:

| id | forma | arquivos aprox. |
|---|---|---|
| `jaffle_shop` | dbt/SQL (.sql + .yml + seeds) | 22 |
| `terraform-aws-s3-bucket` | módulo Terraform (.tf + workflows) | 147 |
| `requests` | biblioteca Python (+ docs/certs) | 131 |
| `click` | biblioteca Python (+ tests) | 181 |
| `axios` | biblioteca TS/JS (.ts minoritário) | 509 |

Clones ficam em `benchmarks/.corpus/realworld/` (gitignored — nunca commitar).

## Uso

```bash
bash benchmarks/realworld/clone_corpus.sh            # baixa/verifica corpus (SHA)
.venv/bin/python benchmarks/realworld/run_benchmark.py
.venv/bin/python benchmarks/realworld/run_benchmark.py --repeats 10 --only requests
.venv/bin/python benchmarks/realworld/run_benchmark.py --keep-data  # inspeciona dbs
```

Saída: `benchmarks/results/realworld-latest.json` (+ doc
`docs/cycle-8-realworld-benchmark.md` com a leitura dos números).

## Guardrails

- **Nunca executa código dos repos indexados** — só `code-atlas index`
  (análise estática tree-sitter); nenhum teste/script/hook do corpus roda.
- **Segredos nunca indexados** — o scanner exclui `SECRET_PATTERNS`
  (`.pem`, `.key`, `.env*`…) antes de hash/inventory; `secrets_skipped`
  é reportado por repo (requests tem certs de teste → skip esperado).
- **Tempo limitado** — timeouts por comando/request; repeats limitados.
- **Isolamento** — `CODE_ATLAS_DATA_DIR`/`CODE_ATLAS_CONFIG_DIR` por repo/modo
  (baseline syntactic-only, determinístico); `PYTHONHASHSEED=0`.
- Números só entram no JSON se produzidos pelo script — nunca transcritos.

## Nota de cobertura (esperado, não defeito)

Repos sem `.py`/`.ts` parseável (jaffle_shop, terraform-aws-s3-bucket) geram
inventário + entidades de domínio, mas **zero símbolos** — o grafo sintático
cobre só `.py/.pyi/.ts/.d.ts/.mts/.cts/.tsx`. `/graph/neighbors` é
`skipped` nesses casos, declarado no resultado.
