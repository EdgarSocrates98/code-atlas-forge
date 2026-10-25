# Cycle 7 — Avaliação medida de extração de domínio (lf-0124)

Framework de avaliação **evidence-first** para os adapters de domínio do
Cycle 7 (SQL, PySpark, AWS, API, Terraform, CI/CD) e para o resolver
cross-domain (`crossresolve`).

Princípio: **toda afirmação abaixo é medida**, não estimada. O runner
executa a pipeline real (`IndexCoordinator` + `DomainRegistry` +
`CrossDomainResolver` no cenário cross) sobre fixtures versionadas, lê o
estado **persistido** (`domain_entities` / `domain_relations` no
snapshot) e compara contra goldens curados em `evaluation/golden/`.

## Como executar

```bash
# relatório + checagem contra goldens (falha com diff legível)
.venv/bin/python evaluation/run_eval.py --check

# apenas relatório (sem checagem)
.venv/bin/python evaluation/run_eval.py

# regerar goldens APÓS inspeção manual do diff — nunca para
# silenciar divergência
.venv/bin/python evaluation/run_eval.py --bless

# integração pytest (mesma comparação, por domínio)
.venv/bin/python -m pytest tests/evaluation/test_domain_eval.py -q
```

Saída: `evaluation/results/domain-eval-latest.json` — contagens por
domínio, quebras por tipo/status/confiança/resolver, diagnósticos,
artefatos vistos por adapter e `golden_check` por cenário.

## Números medidos

Estado persistido por cenário (branch
`cycle-7-domain-intelligence-federation`, fixtures em
`tests/fixtures/`):

| Cenário | Fixture | Entidades | Relações | resolved | unresolved | ambiguous | external | Diag |
|---|---|---|---|---|---|---|---|---|
| `sql` | `sql_warehouse` | 36 | 32 | 32 | 0 | 0 | 0 | 2 |
| `pyspark` | `pyspark_repo` | 37 | 62 | 55 | 7 | 0 | 0 | 9 |
| `aws` | `aws_repo` | 63 | 60 | 59 | 0 | 0 | 1 | 7 |
| `api` | `api_repo` | 30 | 43 | 29 | 9 | 0 | 5 | 15 |
| `terraform` | `terraform_infra` | 24 | 45 | 40 | 0 | 0 | 5 | 3 |
| `cicd` | `cicd_repo` | 13 | 34 | 14 | 3 | 0 | 17 | 2 |
| `crossdomain` | `crossdomain_ws` | 27 | 26 | 20 | 2 | 3 | 1 | 2 |

Precisão contra os goldens curados (`--check`): **todas as arestas
persistidas casam** (`matched == golden`), zero `missing`, zero
`unexpected`, zero entidade proibida — em todos os 7 cenários.

Cross-domain: `cross_considered=4`, `cross_resolved=3`,
`cross_ambiguous=3`, `cross_edges=4`, aplicado `cross_updated=2` +
`cross_inserted=4`. Diagnósticos cross: `endpoint-unmatched`
(`/v1/missing`) e `no-cross-target` (`#/components/schemas/Pets`).

## O que os goldens verificam

- **Contagens exatas** de entidades/relações e quebras por
  kind/type/status/confidence/resolver.
- **Inventário exaustivo de arestas** (set-compare sobre
  tipo+de+para+status+confiança+resolver) — cada cenário lista todas as
  arestas persistidas.
- **Entidades `expected`** — amostra de endpoints que precisam existir
  (ex.: `terraform:module.network@dev`,
  `api:app.main#admin.POST /v2/admin/users/{uid}`).
- **Entidades `forbidden`** — nomes que jamais podem nascer
  (anti-fabricação): expansão de `count`/`for_each`
  (`aws_sqs_queue.workers[0]@dev`), interpolação avaliada
  (`aws_s3_bucket.acme-data-dev@dev`), rota dinâmica
  (`app.GET /edge/{dyn}`), coluna interna de CTE
  (`staging.order_facts.rn`), arquivo não-OpenAPI como contrato
  (`pkg/config.json`), arquivo sem Spark como job (`plain_utils`), e no
  cross-domain o qualificador de catálogo que o spark nunca declarou
  (`pyspark:cat_a.shared.metrics`).
- **`entities.names`** — inventário exato de nomes (set-compare):
  rename/removal/addition vira `missing entity:`/`unexpected entity:`,
  nunca apenas um delta agregado de `total`/`by_kind`.
- **`diagnostics`** — multiconjunto exato de
  `(severity, code, path)`: entradas adversariais das fixtures DEVEM
  produzir diagnóstico na quantidade medida — silêncio (a menos) ou
  ruído (a mais) falham o check.
- **`edges_skipped`** — contagem exata de arestas descartadas por falta
  de endpoint honesto (ex.: `web.legacy` sem parser JS → 2).

## Limitações honestas (medidas, não corrigidas em silêncio)

- **Entradas malformadas produzem diagnóstico, não exceção**: SQL
  quebrado (`99_broken.sql` → 2 `sql-parse-error`), PySpark com
  `SyntaxError`, YAML inválido, workflow com TAB, tf solto — tudo vira
  `parse-error`/`invalid-*`/`stray-*` e o restante do arquivo/repositório
  ainda extrai (o `aws_s3_bucket.survivor` pós-chave-solta nasce).
- **Dinâmico nunca resolve**: targets f-string (`mart.{table_name}`),
  `read.load(out_path)`, `spark.sql(variável)`, rota f-string — ficam
  `unresolved` com marker `dynamic-*`/`templated-*`/`expr`. Sempre.
- **API**: referências de schema (`#/components/schemas/Pets`) e URLs
  dinâmicas ficam `unresolved`; URLs absolutas fora do snapshot são
  `external`. `web/legacy.js` não tem parser JS → `edges_skipped=2`
  (a entidade símbolo existe; a aresta é descartada honestamente).
- **Externo é explícito**: ações GitHub (`actions/checkout`), ambientes
  (`s3://`, `arn:aws:iam::...`), iam_role TF (`aws_iam_role.glue`) —
  marcados `external`, nunca resolvidos para dentro do snapshot.
- **AWS**: identidade de catálogo desconhecida
  (`aws-data:aws:glue.shared_db.dim_customer`) NÃO funde com as
  identidades por-conta (`:111111111111.`/`:222222222222.`) — três
  entidades distintas, medido.
- **CI/CD**: `.gitlab-ci.yml` não entra nos globs de workflow
  (plataforma declared-optional) — zero entidades GitLab; o `matrix`
  os×python é dimensão declarativa, nunca expandido; `run:`/`step:`
  viram alvos textuais `unresolved`.
- **Cross-domain**: `shared.metrics` (dois candidatos `cat_a`/`cat_b`)
  fica `ambiguous` — o resolver não adivinha catálogo que a evidência
  não declarou; `/v1/missing` e `#/components/schemas/Pets` ficam
  `unresolved` com diagnóstico (`endpoint-unmatched`/`no-cross-target`).
- **Sem execução**: adapters observam artefatos estáticos; nenhum job,
  Terraform, pipeline, código ou conexão externa é executado —
  `evidence-first` em tudo.
- **Snapshot isolation**: cada cenário roda em DB próprio sob
  `tmp_path`; o resolver cross-domain opera só dentro do mesmo
  snapshot (nunca cruza repositórios).

## Regeneração de goldens

`--bless` regrava o golden a partir do estado persistido atual. Use
somente após inspecionar o diff de `--check` e confirmar que a mudança
é comportamento correto novo (ex.: adapter ganhou cobertura). Bless
para silenciar regressão é fraude de medição — o golden existe para
detectar mudança.
