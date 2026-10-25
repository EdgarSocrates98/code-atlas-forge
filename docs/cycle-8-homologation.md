# Cycle 8 — Homologação Final

**Versão:** `1.0.0rc1` · **Branch:** `cycle-8-production-quality` · **Base:** `5c20177` (main pós-C7)
**Scorecard consolidado:** `docs/cycle-8-scorecard.md` — **22/32** com deduções honestas.

## Gates executados (todos medidos — `scripts/release_check.sh`)

| gate | resultado |
|---|---|
| release_check.sh (14 gates) | **PASS — FAIL: 0** (pytest, ruff, mypy, wheel+inventory, clean-install, CLI smoke, npm ci, explorer build+unit+e2e, npm audit, pip-audit) |
| pytest | 1510 passed / 1 skipped (último full run) |
| ng test | 238 SUCCESS |
| Playwright e2e | 100%+ specs verdes (a11y 65/65, screenshots 65/65, hardiness 6/6) |
| axe | **48 scans, 0 violações** (24 novas nas views de domínio) |

## Evidência por fase (docs com números verbatim)

| fase | doc | resultado-chave |
|---|---|---|
| 0 baseline | `cycle-8-baseline.md` | gates verbatim, gaps P1/P2 |
| 1 real-world | `cycle-8-realworld-benchmark.md` | 5 repos reais pinados; axios P1 encontrado |
| 2 accuracy | `cycle-8-domain-accuracy.md` | **P=1.000 todos os 6 adapters**, R 0.953–1.0, 9 FN documentados |
| 3 parser/dialect | `cycle-8-parser-compat.md` | axios 503/503; matriz de constructs; crash-per-file isolado |
| 4+5 graph | `cycle-8-graph-hardening.md` | zombie-worker + hidden-edge fixes; 0 leaks em 20 remounts |
| 6 SQLite | `cycle-8-sqlite-domain-search.md` | v11 índices: 30–290× nas queries quentes |
| 7 domain FTS | idem | v12 `domain_fts`, fallback LIKE honesto |
| 8 federation | `cycle-8-federation-scale.md` | `view_id` v2 (resolver/schema no cache key); 5-repo isolado |
| 9 impact | `cycle-8-impact-validation.md` | 12 cenários rotulados: **P/R/F1=1.000, 0 false-confidence** |
| 10 pack value | `cycle-8-context-pack-value.md` | proxy medido; agent-harness = NOT RUN |
| 11 security | `cycle-8-security-audit.md` | **7 findings corrigidos** + 71 testes adversariais |
| 12 supply | `cycle-8-supply-chain.md` | SBOM CycloneDX 1140 comp; pip-audit 0 em runtime; dependabot npm corrigido |
| 14 a11y/visual | `cycle-8-a11y.md` | 48 axe scans 0 viol.; teclado/zoom/motion; 29 screenshots |
| 15 observability | `cycle-8-observability.md` | `/api/v1/diagnostics`, `--verbose`, `CODE_ATLAS_SLOW_MS` |
| 16 release | `docs/RELEASING.md` + `RELEASE-CHECKLIST.md` | `1.0.0rc1`, wheel verificado, 14-gate script |
| 17 competitive | `cycle-8-competitive.md` | matriz + micro-bench medido; "where we lose" 11 itens |
| 18 scorecard | `cycle-8-scorecard.md` | **22/32**, deduções com fonte |
| 19 DX | `architecture.md`, `quickstart.md` | docs re-executados ao vivo; drift do `demo/` corrigido |

## Bugs reais encontrados e corrigidos neste ciclo

1. **TS `import x = require()`** → empty-edge → index inteiro falhava (axios 503 arquivos). Corrigido + dialetos expandidos + crash-per-file isolado.
2. **Zombie worker** — um `Worker` vazado por layout concluído (fa2-layout-runner `finish()` não cancelava engine).
3. **Arestas filtradas renderizavam** — chips de filtro não escondiam edges no sigma (attrs `hidden` agora escritos).
4. **`embedding_cache` nunca persistia via CLI** — `put()` sem commit; agora commit por lote.
5. **`domain neighbors` 500** — precedência `A | B - {None}` deixava `None` no `sorted()`.
6. **Static traversal** `GET /../static-evil` — `startswith` → `is_relative_to`.
7. **Symlink→.env** — secret check agora avalia o alvo, não só o nome do link.
8. **`${{ secrets.* }}` vazava em chaves YAML** — sanitizado em keys/ids/matrix.
9. **Content-Length hostil** (`read(-1)` ~30s) — 400/413 tipado antes do read.
10. **Artifact path confinement** — `artifact_path_confined`+`O_NOFOLLOW` nos 5 adapters.

## Registro consolidado de PARTIAL / NOT RUN

Herdado do scorecard (23 itens) — principais:
- **Angular 18.2.13 EOL** — 8 advisories prod (4 high, reachable-XSS); fix = bump 3 majors → decisão de produto separada. Bloqueia GA honesto, não bloqueia RC.
- **NOT RUN**: Windows/macOS interativo, Firefox/Safari, screen-reader real, Lighthouse, >5k arquivos, 5k-edge ceiling, agent-harness para pack-quality, métricas export/tracing, publicação PyPI/tag.
- **Declared limits**: `.js` não parseado (adapter ausente), `spark.sql` não-literal = dinâmico, pack-only cobre 75% por design.

## Conclusão

Cycle 8 transformou evidência em produção-candidate: precisão dos 6 adapters provada contra ground truth independente (P=1.0), blast-radius validado com 0 falsos-verificados, 10 bugs reais corrigidos, 48 scans de a11y limpos, release gate de 14 checks automatizado e verde. Recomendação: **merge como 1.0.0rc1**; GA bloqueado apenas pelo bump Angular (decisão registrada, não executada).
