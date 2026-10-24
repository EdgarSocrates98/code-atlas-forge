# Cycle 1 — Evidence Report

Produzido no estado de código do commit **`8504c9f`** (5 commits de
implementação, 1 deste relatório/docs adiante). Nenhum push realizado —
repositório local apenas.

## Ambiente

| Item | Valor real |
|---|---|
| OS | Linux 7.0.0-38-generic |
| Python | 3.12.3 |
| code-atlas | 0.1.0 |
| git | 2.43.0 |
| sqlite3 | 3.45.1 (FTS5 disponível) |
| Windows | **NOT RUN localmente** — coberto pela matriz CI `windows-latest` (py3.12/3.13) |

## Comandos executados (verbatim) e resultado

```
python3 -m venv .venv && .venv/bin/pip install -e .[dev]   → OK
.venv/bin/pip wheel . && pip install (não-editável)         → wheel OK
.venv/bin/code-atlas --version                            → code-atlas 0.1.0
.venv/bin/code-atlas doctor                               → 6 checks ok, exit 0
.venv/bin/code-atlas init --root <tmp>                    → config+data+repo registrados
.venv/bin/code-atlas index tests/fixtures/sample_repo --json
  → {"status":"success","files_indexed":5,"secrets_skipped":1,...}
.venv/bin/code-atlas index <repo> --json                  → 116 files, READY
.venv/bin/code-atlas status --json                        → READY, schema v2, git main
.venv/bin/code-atlas index <repo> (repetido)              → {"status":"noop"}
.venv/bin/pytest -q                                       → 115 passed
.venv/bin/ruff check src tests                            → All checks passed
.venv/bin/mypy src                                        → no issues (21 files, strict)
python3 -c "yaml.safe_load" .github/workflows/*.yml       → yaml ok
.venv/bin/python benchmarks/bench_scan.py --files 500 --json
  → {"files_found":500,"cold_scan_s":{"median":0.054},"files_per_s":9324}
.venv/bin/python benchmarks/bench_scan.py --files 5000 --json
  → {"files_found":5000,"cold_scan_s":{"median":0.365},"files_per_s":13702}
```

## Gates do Cycle 1

| Gate | Descrição | Resultado | Evidência |
|---|---|---|---|
| G1 | Independência de Forge | **PASS** | zero deps runtime; audit AST sem forge/claude/codex/network |
| G2 | Segurança | **PASS** | nada executado (teste `setup.py` payload), zero egress (audit), secrets excluídos (count), threat-model.md |
| G3 | Determinismo | **PASS** | manifest bytes idênticos double-run (git+no-git); no-op por digest testado |
| G4 | Consistência | **PASS** | falha injetada preserva READY anterior; FAILED+job inspecionáveis; transação única de publish |
| G5 | Usabilidade Win/Linux | **PASS Linux / CI-only Windows** | `init`/`doctor`/`index`/`status` verdes em Linux; Windows na matriz CI — **não executado localmente** |
| G6 | Contratos | **PASS** | enums idênticos ao Blueprint §5 (teste), identidade lógica≠ocorrência, provenance obrigatória em Relation/Evidence |
| G7 | Qualidade | **PASS** | ruff+mypy strict+115 testes+wheel+smoke verdes |
| G8 | Documentação | **PASS** | quickstart verificado verbatim; limitações explícitas; ADRs; roadmap com gates |

## Limitações reais do Cycle 1 (não é completo)

- **Sem AST/Tree-sitter, sem símbolos, sem arestas reais** — `symbols`,
  `relations`, `evidence`, `fts_symbols` existem só como schema.
- Sem SCIP/LSP, sem busca FTS, sem impact, sem context packs, sem MCP/API,
  sem UI.
- `repositories.root_handle` é o path — mover pasta cria repo novo.
- Fallback `.gitignore` sem git é best-effort (rotulado no manifest).
- Benchmark mede scan+SHA256; no-op coordinator não está no script.

## NOT RUN / pendências honestas

- Execução do CI em GitHub Actions: **NOT RUN** (workflow sintaticamente
  validado com yaml.safe_load; não há push/PR neste ambiente).
- Windows local: **NOT RUN** (máquina é Linux; cobertura prevista em CI).
- pip-audit: **RUN** localmente — `pip freeze | grep -v code-atlas-forge |
  pip-audit -r - --strict` → "No known vulnerabilities found" (o pacote
  local é excluído por não estar no PyPI; job de CI ajustado igualmente).
- Cycle 1 tem **nenhum P0/P1 aberto**; P2s listados nos reviews de cada spec
  (`factory/reviews/`).

## Prontidão para Cycle 2

Base pronta: contratos versionados, ports, snapshot lifecycle, scanner
perimetral, harness de testes e CI. Cycle 2 (Tree-sitter) pode começar
sobre `file_inventory`/`snapshots` existentes sem re-trabalho estrutural.
