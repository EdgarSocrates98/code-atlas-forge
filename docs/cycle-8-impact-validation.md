# Cycle 8 — Validação do blast de impacto (lf-0135, Phase 9)

Medição do conjunto alcançado pelo `code-atlas impact` (modo de
domínio) contra um **corpus rotulado à mão** de 12 cenários
cross-domain, em `evaluation/impact_truth/scenarios.json`.

Distinção dos harnesses existentes:

- `run_eval.py` / goldens (`evaluation/golden/*.json`): inventário do
  comportamento **observado** — "a extração mudou?".
- `run_ground_truth.py` (lf-0129): o que os adapters **extraem** é
  certo? — entidades/relações vs rótulo manual.
- **Este harness**: o que o impacto **alcança** é certo? — o BFS misto
  código+domínio (`impact/domain_traversal.py`) partindo de uma seed
  de entidade real, comparado nó a nó com o blast esperado rotulado a
  partir da leitura das fixtures e da semântica declarada de direção.

## Como executar

```bash
# relatório completo (indexação real por fixture + impacts reais)
.venv/bin/python evaluation/run_impact_validation.py

# um cenário
.venv/bin/python evaluation/run_impact_validation.py \
    --scenario iam-role-removal

# integração pytest (pisos medidos + invariantes duras)
.venv/bin/python -m pytest tests/evaluation/test_impact_truth.py -q
```

Saída: `evaluation/results/impact-truth-latest.json` — por cenário:
linhas/únicos emitidos, TP/FP/FN por classe (`direct`/`transitive`),
conjunto verificado agregado, leaks de confiança, erros de evidência,
violações de `absent`, `unknown_coverage` verbatim, risco e as duas
sondas de truncamento.

## Método

1. **Corpus rotulado** (`impact_truth/scenarios.json`, schema 1): 12
   cenários sobre as 7 fixtures de domínio. Cada rótulo tem
   `direct`/`transitive`/`absent` escritos à mão — `absent` é a lista
   anti-alucinação (nós plausíveis mas errados: fontes irmãs, colunas
   da própria tabela, nós em módulos desligados, pares ambíguos). Os
   nomes esperados são as formas `ImpactCandidate.qualified_name` que
   o engine emite (`dominio:ns.qname` para nós de domínio,
   `qualified_name` nu para símbolos de código).
2. **Pipeline real, superfície do binário**: cada cenário é avaliado
   invocando `cli.main` in-process com `CODE_ATLAS_DATA_DIR` e
   `CODE_ATLAS_CONFIG_DIR` isolados num workspace temporário —
   `index <fixture> --domains ... [--cross-domain]` (adapters opt-in,
   uma indexação por fixture) e `impact symbol <seed> --root <fixture>
   --format json`. Nada do repositório indexado é executado.
3. **Dedup por nó**: o engine emite uma linha por caminho; nós com
   caminhos alternativos colapsam por `(node_type, qualified_name)`
   antes do diff — `rows` e `unique` são ambos reportados.
4. **Contagem**: TP = nó alcançado rotulado na classe correspondente;
   FP = nó alcançado fora de todo rótulo alcançável; FN = rotulado não
   alcançado; `class_mismatch` = alcançado na outra classe (não é FP —
   é classificação errada, reportada à parte). O conjunto
   **verificado** (`direct`+`transitive`, todos `strength=verified`)
   também é pontuado agregado. P/R `null` quando o denominador é zero
   — nunca 1.0 fabricado.
5. **Invariantes duras** (orçamento zero, asserções no pytest):
   - *false-confidence*: `strength=verified` exige `fully_verified` +
     todos os hops `resolved`; `impact=direct|transitive` exige
     `verified`; `possible|unknown` nunca pode ser `verified`.
   - *evidência*: `hops == len(edge_refs) == len(statuses)`;
     `affected_symbol_key`/`intermediate_keys[-1]` == `symbol_key`;
     todo `edge_refs` (`dom:N`→`domain_relations`, `rel:N`→`relations`)
     e toda ocorrência (`domain_occurrence_id`/`occurrence_id`)
     existem no snapshot alvo.
   - *truncamento* (duas sondas por cenário): `--max-paths = full_rows`
     → a emissão atinge o cap e `truncated` **deve** ser declarado
     (prefixo no limite é honestamente marcado, mesmo com conjunto
     exato); `--max-paths = full_rows + 3` → traversal termina
     naturalmente, `truncated` **deve** ser `false` e todas as linhas
     têm que sair.
6. **Seed independente**: `resolve_entity_refs` contra o banco mede
   quantas entidades a seed casou — um blast vazio com seed não
   resolvida seria medição inválida, não recall.

## Cenários e números medidos

Branch `cycle-8-production-quality`, run de
`.venv/bin/python evaluation/run_impact_validation.py` (workspace
temporário por run, snapshots isolados por repositório):

| Cenário (classe) | Fixture | Seed | Rows | Únicos | direct | transitive | P | R | F1 |
|---|---|---|---|---|---|---|---|---|---|
| `sql-table-change` | `sql_warehouse` | `landing.orders` | 14 | 10 | 3/3 | 7/7 | 1.000 | 1.000 | 1.000 |
| `iceberg-table-change` (Iceberg) | `aws_repo` | `lake.db.orders` | 6 | 4 | 4/4 | 0/0 | 1.000 | 1.000 | 1.000 |
| `pyspark-job-change` | `pyspark_repo` | `etl_orders` | 0 | 0 | 0/0 | 0/0 | — | — | — |
| `pyspark-table-change` | `pyspark_repo` | `staging.orders` | 14 | 10 | 5/5 | 5/5 | 1.000 | 1.000 | 1.000 |
| `api-endpoint-removal` | `api_repo` | `GET /pets/` | 4 | 4 | 2/2 | 2/2 | 1.000 | 1.000 | 1.000 |
| `api-contract-endpoint-removal` | `api_repo` | `GET /v1/pets` | 2 | 2 | 1/1 | 1/1 | 1.000 | 1.000 | 1.000 |
| `openapi-spec-change` | `api_repo` | `openapi/petstore.yaml` | 1 | 1 | 1/1 | 0/0 | 1.000 | 1.000 | 1.000 |
| `terraform-module-change` | `terraform_infra` | `module.network` | 3 | 2 | 2/2 | 0/0 | 1.000 | 1.000 | 1.000 |
| `iam-role-removal` | `terraform_infra` | `aws_iam_role.glue` | 30 | 16 | 6/6 | 10/10 | 1.000 | 1.000 | 1.000 |
| `gh-actions-change` | `cicd_repo` | `reusable.yml#Reusable Checks` | 4 | 4 | 1/1 | 3/3 | 1.000 | 1.000 | 1.000 |
| `cross-repo-dependency` | `crossdomain_ws` | `sql:staging.orders` | 7 | 5 | 1/1 | 4/4 | 1.000 | 1.000 | 1.000 |
| `api-consumer-call` | `crossdomain_ws` | `POST /v1/pets` | 5 | 5 | 2/2 | 3/3 | 1.000 | 1.000 | 1.000 |

**Micro-agregado (conjunto verificado): 63 TP, 0 FP, 0 FN —
precision 1.000, recall 1.000, F1 1.000.** Por classe: direct 28/28,
transitive 35/35. Candidatos possíveis emitidos: **0** em todos os
cenários (todo nó alcançado chegou por caminho com todos os hops
`resolved` — quando o caminho não prova, o nó simplesmente não é
emitido e vira `unknown_coverage`).

### Invariantes medidos

| Invariante | Resultado |
|---|---|
| `possible→verified` leaks | **0** em 86 linhas emitidas (orçamento zero, assertado) |
| `absent` violações | **0** — nenhum nó proibido alcançado em nenhum cenário |
| Erros de evidência | **0** — todo `edge_refs` resolve em row real do snapshot alvo |
| Truncamento desonesto | **0** — sonda at-cap declara em 11/11 cenários não-vazios; sonda over-cap não declara e emite tudo em 12/12 |
| `unknown_coverage` | reportado nos 3 cenários com deps não-traversáveis |
| Blast vazio honesto | `pyspark-job-change`: seed resolvida (1 entidade), 0 candidatos |

## O que foi verificado adversarialmente (e está correto)

- **Direção de aresta não inventa dependentes**: seedar o agregado
  `etl_orders` (job PySpark) alcança **0** nós — `CONTAINS` só
  propaga parte→agregado. As transformações/tabelas do job mudam COM
  ele, não POR causa dele: blast vazio é a resposta certa, medida
  (não assumida — a seed casa 1 entidade).
- **Tabela ≠ colunas**: `landing.orders` não alcança
  `landing.orders.order_id` etc. — colunas são entidades próprias com
  arestas próprias, não “partes” do nó tabela.
- **Fontes irmãs não são dependentes**: `staging.customers` alimenta
  as mesmas views que `staging.orders` mas nunca entra no blast dele
  (sql e pyspark).
- **Upstream não é blast**: `updates_src` (fonte do merge Iceberg) e
  `s3://inbound-bucket/orders` ficam fora do blast de
  `lake.db.orders` — `DERIVES_FROM` propaga de→para, não volta.
- **Dependência não-traversável vira incerteza, nunca nó**: contratos
  externos/ambíguos contam em `unknown_coverage`
  (`domain-external-deps:3` no IAM, `7`+`1` no CI/CD,
  `domain-unresolved-deps:1` no contrato API) e em
  `uncertain_deps` dos candidatos — nenhum endpoint externo virou
  blast “verified”.
- **Módulos desligados ficam fora**: `module.shared`/`module.extra`
  e a árvore `module.network` não entram no blast do IAM role; os
  `unresolved:broken.*` de `broken.tf` idem.
- **Ambiguidade cross-domain não cola**: `shared.metrics` existe como
  `sql:cat_a`, `sql:cat_b` e `pyspark` no workspace — nenhuma das três
  entra no blast de `sql:staging.orders` (a aresta ambígua não é
  percorrida como identidade).
- **Ponte domínio→símbolo funciona nos dois sentidos**: o endpoint
  FastAPI removido alcança o handler `app.routers.list_pets` +
  módulo + arquivo (via `DEFINES` acoplado); o endpoint OpenAPI
  alcança o caller `app.client.post_pet` (via `CALLS_ENDPOINT`
  resolvido pelo `CrossDomainResolver`) + módulo + arquivo.
- **Seed forma importa**: `staging.orders` nu casa as duas entidades
  homônimas (multiplicidade honesta); `sql:staging.orders` casa só a
  do domínio pedido — o corpus usa a forma qualificada no cenário
  cross-domain.

## Limites declarados

- **“cross-repo” é medido na camada de domínio**: isolamento de
  snapshot impede blast entre repositórios **por design** — os dois
  cenários `cross-repo-dependency`/`api-consumer-call` medem a ponte
  cross-adapter (REFERENCES de identidade + `CALLS_ENDPOINT`
  resolvido) dentro de um workspace, que é o que o usuário quer
  dizer com “dependência entre serviços”. Um blast literal
  repo→repo seria violação de `snapshot_resolver` e não existe.
- **`max_nodes`/`max_depth` não levantam `truncated`**: só
  `max_paths` marca o flag (o cap SQL de `max_nodes` corta a lista de
  vizinhos silenciosamente). Com os defaults (500/4) nenhum cenário
  encosta — limitação de observabilidade registrada, não bug
  exercitado aqui.
- **Rotulagem single-evaluator**: mitigada por `absent` explícito e
  pelo diff determinístico — qualquer divergência rótulo↔engine vira
  linha revisável em `impact-truth-latest.json`.
- **Déficits medidos**: nenhum neste run (0 FN). Se um aparecer, ele
  bloqueia `test_per_class_recall`/`test_summary_aggregates` até ser
  classificado — os pisos são os valores medidos, não aspiracionais.

## Rastreabilidade

- Corpus: `evaluation/impact_truth/scenarios.json`
- Harness: `src/code_atlas/eval/impact_truth.py`
- Runner: `evaluation/run_impact_validation.py`
- Resultados: `evaluation/results/impact-truth-latest.json`
- Testes: `tests/evaluation/test_impact_truth.py`
