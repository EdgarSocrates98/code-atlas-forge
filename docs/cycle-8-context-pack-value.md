# Cycle 8 — Valor medido do context pack (lf-0136, Phase 10)

Medição do valor dos packs de contexto de domínio em 9 tarefas reais
("what breaks if X changes", "who calls endpoint Y") sobre as fixtures
de domínio — proxy estrutural honesto, com fatos rotulados à mão.

## O que NÃO é medido (declarado)

- **`agent_quality`: NOT RUN.** Não existe LLM/agent loop neste
  repositório — não há harness para medir se uma resposta final é
  melhor. O campo `agent_quality` do resultado diz `NOT RUN`; nada
  aqui simula qualidade de agente.
- O que se mede é o **proxy pedido pela spec**: tokens do pack vs
  fatos contidos vs queries necessárias para montar a mesma
  informação — mais o baseline "engenheiro com grep".

## Como executar

```bash
.venv/bin/python evaluation/run_pack_value.py
.venv/bin/python evaluation/run_pack_value.py --task iam-role-glue
.venv/bin/python -m pytest tests/evaluation/test_pack_value.py -q
```

Saída: `evaluation/results/pack-value-latest.json`.

## Método

Corpus `evaluation/pack_value/tasks.json` (schema 1): 9 tarefas, cada
uma com pergunta real, seed de entidade (mesma forma do lf-0135),
`grep_seeds` (o literal que um humano buscaria) e `facts` — os
`qualified_name` verificados em lf-0135 que uma resposta correta
precisa conter.

Quatro braços, todos reais/determinísticos (pipeline via `cli.main`
in-process, `CODE_ATLAS_*_DIR` isolado; nada do repo indexado é
executado):

| Braço | O que é | Queries | Tokens |
|---|---|---|---|
| `no_atlas` | Simulação grep+read: busca literal (substring crua, como `grep -l`) → abre arquivos que casam → nomes encontrados viram próximas buscas; cap de 5 rodadas | 1 por rodada | chars/4 dos arquivos **abertos** |
| `pack_only` | `DomainContextEngine.build_domain` real (budget 4000) | 1 | `tokens_payload` estimado pelo próprio engine |
| `atlas_impact` | `impact symbol <seed> --format json` | 1 | chars/4 do payload JSON |
| `atlas_search` | `domains entities --q` + `domains lineage --both --depth 8` | 2 | chars/4 das saídas |

Detecção de fato: match com fronteira de token sobre as formas reais
da entidade resolvidas em `domain_entities` (`qualified_name`,
display, basename, caminho de `file#N`). `orders` não casa dentro de
`enriched_orders` nem de `staging.orders` — e o fato `file#N` é
creditado quando o **arquivo** que o contém é lido (granularidade
honesta: o agente vê a statement, não o índice `#N`).

## Números medidos

Branch `cycle-8-production-quality`, run de
`.venv/bin/python evaluation/run_pack_value.py`:

| Tarefa | fatos | no-atlas q/f/tok | pack f/tok | impact f/tok | search q/f/tok |
|---|---|---|---|---|---|
| `blast-landing-orders` | 10 | 3 / 10 / 592 (4/4 arq.) | 5 / 1823 | 10 / 4948 | 2 / 10 / 3465 |
| `blast-iceberg-orders` | 4 | 2 / 4 / 213 (1/14) | 4 / 2533 | 4 / 1964 | 2 / 4 / 3263 |
| `blast-spark-orders` | 10 | 2 / 10 / 520 (3/7) | 9 / 2459 | 10 / 4591 | 2 / 10 / 2775 |
| `endpoint-pets-get` | 4 | 2 / 4 / 747 (6/12) | 4 / 1375 | 4 / 1070 | 2 / 3 / 1433 |
| `terraform-module` | 2 | 2 / 2 / 282 (4/13) | 2 / 1559 | 2 / 1161 | 2 / 2 / 885 |
| `iam-role-glue` | 16 | 2 / 13 / 939 (8/13) | 9 / 3233 | 16 / 10236 | 2 / 5 / 839 |
| `cicd-reusable` | 4 | 2 / 4 / 329 (2/5) | 4 / 1706 | 4 / 1762 | 2 / 4 / 2035 |
| `xdomain-sql-orders` | 5 | 2 / 5 / 369 (2/5) | 4 / 1175 | 5 / 2575 | 2 / 4 / 998 |
| `who-calls-post-pets` | 1 | 2 / 1 / 197 (2/5) | 1 / 1441 | 1 / 1426 | 2 / 0 / 650 |

**Agregado (56 fatos esperados):**

| Braço | queries | tokens | fatos | cobertura |
|---|---|---|---|---|
| `no_atlas` | 19 | 4 188 | 53 | 0.946 |
| `pack_only` | 9 | 17 304 | 42 | 0.750 |
| `atlas_impact` | 9 | 29 733 | 56 | **1.000** |
| `atlas_search` | 18 | 16 343 | 42 | 0.750 |

## Leitura honesta

1. **`impact` é a superfície certa para "what breaks"**: 56/56 fatos
   verificados, 1 query por tarefa. O JSON é o payload mais caro
   (~3.3k tokens/tarefa) porque carrega paths de evidência completos —
   é o preço da prova, não de recheio.
2. **O pack é bundle de grounding, não resposta de blast**: semente
   tipada + vizinhos 1-hop + contadores por status + expansion refs —
   42/56 (75%) nos blast por desenho (fatos a 2+ hops ficam fora;
   `neighbors_omitted`=0 em todas as tarefas, nada foi cortado por
   budget). Em perguntas fora de fluxo-de-dados ele cobre o que a
   busca genérica não vê: `iam-role` 9 vs `search` 5 (PROVISIONS
   acoplado + CONTAINS), `who-calls` 1 vs 0 (`CALLS_ENDPOINT` não é
   `DATA_FLOW_TYPE`).
3. **`entities`+`lineage` não substituem `impact`**: 42/56 (75%) com
   2× as queries — `lineage` só percorre `READS_FROM`/`WRITES_TO`/
   `TRANSFORMS`/`DERIVES_FROM`; `CONTAINS`, `REFERENCES` de identidade,
   `CALLS_ENDPOINT` e as pontes domínio→símbolo ficam invisíveis
   (`who-calls-post-pets`: 0/1).
4. **O grep funciona — até onde o nome existe como texto**: 53/56
   (94.6%) com 19 queries e só ~4k tokens **de arquivos abertos** —
   fixtures pequenas favorecem. Os 3 fatos perdidos são todos HCL em
   que declaração ≠ referência (`module "root"` vs `module.root`,
   `resource "aws_sqs_queue" "workers"` vs `aws_sqs_queue.workers`,
   idem `aws_cloudwatch_metric_alarm.depth`): a forma pontilhada que o
   atlas indexa **nunca aparece como texto** — é conhecimento
   extraído, não grepável.
5. **Presença de nome ≠ dependência verificada**: os fatos do braço
   `no_atlas` são "nome visto em arquivo aberto" — coocorrência. O
   braço não sabe dizer se `staging.orders` mencionado é realmente
   downstream (falso-positivo invisível nesta métrica), não produz
   `unknown_coverage`, não distingue `verified` de `possible` e não
   emite evidência — o que os braços atlas fazem por construção.

## Limites declarados

- **Escalas**: fixtures de 4–14 arquivos — o custo `no_atlas`
  (arquivos abertos × queries iterativas) cresce com o repo; aqui já
  precisou de 8/13 arquivos no `iam-role-glue`. Nada diz sobre repos
  grandes.
- **Crédito por arquivo**: fatos `file#N` contam quando o arquivo é
  lido — granularidade justa ("o agente vê a statement"), mas não é
  prova de que ele a relacionaria corretamente.
- **Forms do match**: fato considerado encontrado quando uma forma
  real aparece com fronteira de token — aproximação honesta de
  "conteúdo answerable", não juízo de resposta.
- **Pack tokens** são estimativa chars/4 do engine
  (`tokens_estimated=true`), nunca contagem real de tokenizer.
- **Qualidade de agente**: NOT RUN — ver topo.

## Rastreabilidade

- Corpus: `evaluation/pack_value/tasks.json`
- Harness: `src/code_atlas/eval/pack_value.py`
- Runner: `evaluation/run_pack_value.py`
- Resultados: `evaluation/results/pack-value-latest.json`
- Testes: `tests/evaluation/test_pack_value.py`
