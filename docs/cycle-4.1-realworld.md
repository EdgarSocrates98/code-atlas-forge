# Cycle 4.1 — Avaliação real-world (corpus vendored)

Corpus **commitado** em `tests/corpus/realworld/` — sem rede, sem
dependência de repositórios públicos (Grill Gate). Dois mini-projetos
escritos para o eval (sem questão de licença):

- `pyapp/` — WSGI app Python: models, db em memória, services,
  routes, wsgi, testes (7 arquivos).
- `tsapi/` — API TypeScript: types, store, auth, server (4 arquivos).

**Identidade**: ambos herdam o `identity_key` do git remote do
hospedeiro → mesmo `repo_id` (design). O harness isola dando **um db
por projeto** — `tests/golden/realworld_eval.py`.

## Tarefas (8 categorias da spec, judgments graduados)

`tests/golden/realworld_judgments.json`: locate-implementation,
understand-component, explain-dependency, identify-consumers,
prepare-refactoring, find-tests, investigate-bug, plan-modification.

## Métricas medidas (K=10, macro média das 8 tarefas)

| estratégia | hit@10 | recall@10 | ndcg@10 | mrr | ap@10 |
|---|---|---|---|---|---|
| structural | 0.250 | 0.135 | 0.208 | 0.250 | 0.135 |
| lexical | 0.625 | 0.419 | 0.455 | 0.525 | 0.350 |
| hybrid | 0.625 | **0.542** | 0.419 | 0.458 | 0.352 |

## Achados honestos

- Hybrid **empata lexical em hit-rate e AP**, mas lidera recall
  (0.542 vs 0.419) — fusão recupera símbolos que a lexical perde.
- Structural substring falha em queries NL ("who calls X") — é o
  baseline esperado, não um defeito.
- Nenhuma métrica foi inventada; `agent_e2e` marcado `not_run`
  (sem agente autorizado no ambiente).
- Diversidade = 1.0 em todas — corpus pequeno, hits únicos.

## Como reproduzir

```bash
.venv/bin/python -m pytest tests/golden/test_realworld_eval.py -q
.venv/bin/python -c "import sys,tempfile,json;sys.path.insert(0,'tests/golden');from realworld_eval import run;from pathlib import Path;print(json.dumps(run(Path(tempfile.mkdtemp())),indent=1))"
```
