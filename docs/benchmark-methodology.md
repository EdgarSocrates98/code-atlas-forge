# Metodologia de Benchmark — Cycle 1

## Princípio

Números só existem se produzidos por script commitado e reproduzível.
Nada de valores transcritos, estimados ou "de outra máquina" sem nota.

## Corpus

`benchmarks/bench_scan.py` gera árvore sintética determinística
(`random.Random(20261024)`): N arquivos, extensões sorteadas
(`.py .ts .js .md .json .txt .go .rs`), tamanhos 64B–16KiB, profundidade
0–3. Mesmos parâmetros → mesma árvore e mesmos SHA-256.

## Métricas medidas (Cycle 1)

| Métrica | Fonte | Como |
|---|---|---|
| files encontrados | `ScanResult.files` | contagem |
| bytes totais | soma size_bytes | inventário |
| cold scan | `time.monotonic` × repeats, mediana | walk+hash completo |
| warm scan | idem, segunda passada | cache de FS do SO |
| files/s | files / mediana cold | throughput |

## Não medido ainda (NOT RUN — ciclos futuros)

- **No-op speedup real no coordinator** (digest → skip): existe no código,
  mas o benchmark mede scan, não o atalho de digest. Cycle 3.
- **Memory peak** sob corpus grande. Cycle 8.
- **Index time end-to-end** (scan+persist) em corpora 100k+. Cycle 8.
- **Query latency** — não há queries de busca ainda.
- **Baseline competitivo** (ctags/SCIP/others) — Cycle 8 exige comparação.

## Reprodução

```bash
.venv/bin/python benchmarks/bench_scan.py --files 500 --json
.venv/bin/python benchmarks/bench_scan.py --files 5000 --repeats 5 --json
```

Baseline de referência medido (Linux, py3.12.3): ~9.3k–13.7k files/s em
corpus sintético — ver `benchmarks/README.md` para o JSON completo.

## Regras

- Hardware/OS/Python sempre anotados junto ao número.
- Limites do scanner nunca relaxados para inflar resultado.
- Divergência >2× entre runs no mesmo hardware = investigar antes de citar.
