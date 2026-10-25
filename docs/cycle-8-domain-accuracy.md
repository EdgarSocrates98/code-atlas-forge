# Cycle 8 — Acurácia medida dos adapters de domínio (lf-0129, Phase 2)

Medição de precision/recall dos 6 adapters de domínio (SQL, PySpark,
AWS, API, Terraform, CI/CD) contra **verdade-terreno independente**:
rótulos escritos à mão a partir da leitura de cada arquivo das
fixtures, sem consultar a saída dos adapters.

Distinção do harness golden (lf-0124): os goldens
(`evaluation/golden/*.json`) são inventários curados do comportamento
**observado** — respondem "o adapter mudou?". Os truth sets
(`evaluation/ground_truth/*.json`) respondem "o adapter está
**certo**?" — entidades e relações que *devem* existir, itens que
*nunca* podem ser fabricados, e casos cujo resultado correto é
`unresolved`/`external`/ausência.

## Como executar

```bash
# relatório completo (pipeline real por fixture, diff rótulo↔extração)
.venv/bin/python evaluation/run_ground_truth.py

# um domínio
.venv/bin/python evaluation/run_ground_truth.py --domain aws

# integração pytest (pisos medidos, declaração abaixo)
.venv/bin/python -m pytest tests/evaluation/test_ground_truth.py -q
```

Saída: `evaluation/results/ground-truth-latest.json` — TP/FP/FN por
classe (entidade, relação, diagnóstico), precision/recall/F1 por
classe e micro-agregado por domínio, e as linhas legíveis de cada
`missing`/`fabricated`/`unexpected`/`kind_mismatch`.

## Método

1. **Rotulagem manual** (`evaluation/ground_truth/<domain>.json`):
   cada fixture foi lida arquivo a arquivo. Regras `expected`
   (entidades por nome/prefixo + `kind`, relações por
   tipo/de/para/status), regras `forbidden` (anti-fabricação),
   `patterns_forbidden` (segredos/valores sensíveis que jamais podem
   persistir) e `diagnostics_expected`.
2. **Auditoria exaustiva** (`complete: true` em todas as classes):
   todo item extraído precisa casar um rótulo `expected` — qualquer
   coisa fora do rótulo vira FP `unexpected`. Ou seja, **precision é
   medido sobre a extração inteira**, não sobre uma amostra — mais
   forte que o mínimo de ~10–30 fatos amostrados da spec.
3. **Casos adversariais**: tabelas homônimas em catálogos distintos
   (AWS cross-account), referências dinâmicas (`${var}`, f-strings,
   parâmetros, `spark.sql` não-literal, URLs template), `SELECT *`,
   arquivos quebrados (devem render parcial honesto + diagnóstico,
   nunca entidades fantasmas) e valores sensíveis de
   `tfvars`/DefaultArguments (nunca podem vazar para entidades,
   atributos ou arestas).
4. **Contagem**: TP = item extraído coberto por rótulo `expected`;
   FN = fato rotulado ausente (inclui shortfall de `count` e
   `kind` divergente); FP = item casando `forbidden` + excedente de
   `count` + qualquer item não rotulado (modo `complete`).
   `unaudited` reporta itens fora de todo rótulo — **0 em todos os
   domínios** (auditoria total).

## Números medidos

Branch `cycle-8-production-quality`, fixtures em `tests/fixtures/`,
run de `.venv/bin/python evaluation/run_ground_truth.py`:

| Domínio | Entidades (ext) | Relações (ext) | TP | FP | FN | Precision | Recall | F1 |
|---|---|---|---|---|---|---|---|---|
| `sql` | 36 | 32 | 69 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `pyspark` | 37 | 62 | 104 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `aws` | 63 | 60 | 125 | 0 | 5 | 1.000 | 0.962 | 0.980 |
| `api` | 30 | 43 | 81 | 0 | 4 | 1.000 | 0.953 | 0.976 |
| `terraform` | 24 | 45 | 72 | 0 | 0 | 1.000 | 1.000 | 1.000 |
| `cicd` | 13 | 34 | 49 | 0 | 0 | 1.000 | 1.000 | 1.000 |

Por classe: entidades P=1.000 em todos; R=1.000 exceto `aws` 0.954 e
`api` 0.938. Relações P=1.000 em todos; R=1.000 exceto `aws` 0.968 e
`api` 0.956. Diagnósticos esperados: todos presentes (FN=0).
`patterns_forbidden`: **0 violações** — nenhum segredo
(`hunter2`, `p4ssw0rd`, `sup3r-s3cret-do-not-leak`,
`tok-live-adversarial`, `tfvars-secret-must-not-leak`) ou nome de
segredo de pipeline vazou para entidades, atributos, arestas ou
diagnósticos.

**Leitura honesta: precision 1.000 em todos os 6 adapters — nenhuma
fabricação medida** (casos dinâmicos/adversariais ficam
`unresolved`/`external`/ausentes, como deve ser). Todos os déficits
estão no lado do recall e estão listados abaixo, um a um.

## Déficits medidos (todos os FN — nenhum escondido)

### `aws` — recall 0.962 (5 FN)

- **[gap]** `s3://emr-bucket/jars/notify.jar` ausente (entidade +
  aresta `REFERENCES` do step `notify`). `emr/steps.json` tem o literal
  em `HadoopJarStep.Jar`; o adapter lê `Args` mas não `Jar` — o
  artefato executável do step fica fora do grafo.
- **[gap]** `s3://emr-logs-bucket/clusters/` ausente (entidade +
  aresta `REFERENCES` do cluster). `LogUri` em `emr/cluster.yaml` é da
  mesma classe do warehouse Iceberg (capturado); não é extraído.
- **[inconsistência]** `aws-data:orders_src` persistido como
  `data_table`, mas é `createOrReplaceTempView` — o adapter `pyspark`
  emite `data_view` para o mesmo construto (`orders_v`). A entidade
  existe (não é fabricação); a classificação diverge entre adapters.

### `api` — recall 0.953 (4 FN)

- **[gap, declarado pelo adapter]** Endpoints montados
  `GET /api/pets/` e `GET /api/pets/{pet_id}` ausentes (2 FN). Em
  `app/main.py`, `app.include_router(pets_router, prefix="/api")` sobre
  um router **importado de outro arquivo**: o prefixo de montagem
  cross-file não é dobrado na identidade do endpoint (o adapter emite o
  diagnóstico `cross-file-router-prefix` — limitação declarada, medida
  como falta real contra o caminho servido). Mounts no mesmo arquivo
  dobram corretamente (`POST /v2/admin/users/{uid}`, Express
  `/api/items`).
- **[degradação, contada]** `CALLS_ENDPOINT web.legacy → /v1/pets` e
  `DEFINES web.legacy → GET /legacy` ausentes (2 FN). `web/legacy.js`
  não tem parser de símbolos JS — as arestas são emitidas pela extração
  e **descartadas na persistência** por falta de âncora
  `from_symbol_occ` (`edges_skipped=2`, contado, não silencioso). A
  rota `GET /legacy` é extraída como entidade (âncora de entidade
  basta); só as pontes símbolo→domínio caem.

## O que foi verificado adversarialmente (e está correto)

- **Dinâmico nunca resolve**: `saveAsTable(f"mart.{table_name}")`,
  `spark.sql(query)`, `read.parquet(out_path)` e `spark.table(name)`
  ficam `unresolved` com texto honesto — nenhuma tabela fabricada
  (`mart.{table_name}` não existe como entidade). URLs f-string e
  `${cfg.base}` idem (`dynamic:` + `heuristic`, nunca `resolved`).
- **Homônimos não colidem**: `shared_db.dim_customer` existe 3× como
  identidades distintas (`aws:glue:111…`, `aws:glue:222…`, sem escopo) —
  nenhuma fusão, nenhum quarto catálogo inventado. `lake_db.agg_orders`
  existe nos escopos spark-default e glue-catalog separadamente.
- **Arquivos quebrados degradam honestamente**: `99_broken.sql`
  recupera 2 SELECTs + 2 `parse-error`; `broken_job.py` emite
  diagnóstico e zero entidades; `broken.tf`/`stray.tf` viram
  `unresolved:broken.*` (não-provisionadas); `malformed.yaml` salva 2
  endpoints + `flow-parse-error`; `broken.yml` salva workflow + job com
  `yaml-tab-indent`.
- **Sem expansão fabricada**: `count`/`for_each` e matriz GH Actions
  não materializam instâncias (`workers[0]`, `test[ubuntu]` não
  existem); interpolações `${var}` não viram nomes de bucket;
  colunas de CTE (`rn`, `ranked`) não vazam; `COUNT(*)` não ganha
  coluna-fonte inventada.
- **Escopo de suporte respeitado**: `.gitlab-ci.yml`, `docs/notes.txt`,
  `pkg/config.json`, `not_a_spec.yaml`, `plain_utils.py` — nenhum vira
  entidade ou aresta.
- **Resolução intra-domínio não usurpa o resolver cross-domain**:
  `CALLS_ENDPOINT` nunca sai `resolved` dentro de um único domínio
  (a amarração consumidor↔contrato é do `CrossDomainResolver`, medida
  no cenário `crossdomain` do harness golden).

## Pisos declarados no pytest

`tests/evaluation/test_ground_truth.py` fixa: `precision ≥ 1.0` em
todos os domínios (orçamento zero de fabricação — `fp == 0` sempre);
`recall ≥` valor medido (`aws` 0.96, `api` 0.95, demais 1.0);
`fn ≤` contagem medida de déficits (`aws` 5, `api` 4, demais 0) —
qualquer FN novo falha até ser classificado; `unaudited == 0` (o
truth set continua cobrindo 100% da extração). Os pisos são os valores
medidos deste ciclo — subir recall exige corrigir o déficit ou
reclassificar o rótulo com justificativa, nunca afrouxar o piso.

## Escopo e limites

- Cenário `crossdomain` fora do escopo — é medição do
  `CrossDomainResolver` (resolver, não adapter), já coberta pelo
  harness golden de lf-0124.
- `edges_skipped` (persistência sem âncora) aparece como FN medido
  apenas em `api` — nos demais domínios é 0.
- Rotulagem é de um único avaliador; regras `count`/`complete`
  tornam qualquer divergência rótulo↔extração explícita e revisável
  em `ground-truth-latest.json`.
