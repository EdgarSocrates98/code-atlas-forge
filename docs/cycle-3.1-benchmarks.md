# Benchmarks — Cycle 3.1 (lf-0031)

> Metodologia e números completos:
> `benchmarks/results/lf-0031-sqlite-perf.json`. Corpus: sintético de
> **5000 arquivos** (`synth_indexable_corpus` de
> `benchmarks/bench_index.py`), cenário "1 arquivo alterado →
> `index(incremental=True)`", 3 medições, mediana, sem tracemalloc.

## Before → After (mesmo corpus/metodologia)

| run | total (s, mediana) | graph_build | persist_publish |
|---|---|---|---|
| before (HEAD `9351373`) | 5.48 | ~2.0–2.2s (perfil) | ~1.3–1.5s |
| after `get_many` só | 5.28 | idem | idem |
| after `get_many` + lookup O(1) | **4.55** | **0.93–1.30s** | 1.4–1.5s |

## Claims sustentados

- `graph_build` **−50%** (2.0–2.2s → 0.93–1.3s): `_resolve_relations`
  fazia scan linear em `_modules` por arquivo (O(n²) — ~1.15s tottime);
  trocado por lookup O(1) nos mapas `_by_path`/`_nid_mod` já existentes.
- Total incremental 1-arquivo/5k: **−17%** (5.48 → 4.55s).
- Cache lookup: 2019 round-trips `SELECT` → ~6 (chunks de 400 via
  `json_each`, SQL estático); wall-clock **neutro** — o custo real do
  estágio é `result_from_payload` (desserialização ~1s). Claim honesto:
  o batch reduz round-trips, não latência, neste cenário.
- Persist é o maior custo remanescente (~1.4–1.5s, O(snapshot)):
  ~110k linhas reescritas por snapshot completo — consequência do
  modelo ADR-0020 (fragments rejeitados em lf-0030).
- Nenhuma duplicata de relations/occurrences no snapshot medido
  (auditado por GROUP BY).
- Perfis intermediários: `_resolve_relations` 1.76s cum, scanner
  hashing 0.62s, `insert_relations` 0.52s.

## Decisões documentadas

- **PRAGMAs inalterados** (WAL, `synchronous=NORMAL`, `foreign_keys=ON`,
  `busy_timeout`, `cache_size` do Cycle 3) — nenhuma durabilidade
  enfraquecida.
- **GC de snapshots**: `retain_snapshots` opt-in (default `0` =
  ilimitado, preserva auditoria); READY/FAILED nunca deletados; mesma
  transação do publish. `VACUUM` avaliado e **rejeitado** como
  operação automática (bloqueia readers WAL, sem ganho funcional) —
  ver `docs/storage-lifecycle.md`.
- Instrumentação `stage_ms_*` com `time.monotonic()` — overhead
  desprezível (lição Cycle 3: tracemalloc inflava 3–4×).

## Regressão de performance

`bench_index.py --sizes 100` (gate da spec): total 0.2245s,
peak_rss 32.6MB, p50 query 0.74ms — sem regressão no tamanho pequeno.
