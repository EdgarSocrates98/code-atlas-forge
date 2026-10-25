# Cycle 8 — Baseline Audit (Phase 0)

Spec: `factory/specs/inbox/lf-0127-cycle8-baseline-audit.md`
Branch: `cycle-8-production-quality` · Baseline HEAD: `405a1ad6cb77f7bfb86f6f3c953859914df98468`
(`cycle-8: spec backlog lf-0127..0146`, 2026-10-25 11:11:28 -0300)

## Ambiente

| item | valor |
|---|---|
| OS | Linux 7.0.0-38-generic x86_64, 16 cores |
| Python | 3.12.3 (`.venv`) |
| ruff | 0.17.2 |
| mypy | 2.4.0 (compiled: yes) |
| node / npm | v22.23.2 / 10.9.8 |
| Angular | 18.2.13 (`@angular/core`, `@angular/common`, `@angular/compiler`) |
| Chrome Headless | 156.0.0.0 (Linux) |

## Comandos executados (saídas verbatim)

### `git rev-parse HEAD`

```
405a1ad6cb77f7bfb86f6f3c953859914df98468
```

### `.venv/bin/python -m pytest tests/ -q`

```
=========================== short test summary info ============================
SKIPPED [1] tests/api/test_files_occurrences.py:162: todo arquivo tem ocorrências
1188 passed, 1 skipped in 58.92s
```

→ **1188 passed / 1 skipped** (skip declarado: assertion `todo arquivo tem ocorrências`).

### `.venv/bin/ruff check src tests`

```
All checks passed!
```

(exit 0)

### `.venv/bin/mypy src`

```
Success: no issues found in 163 source files
```

(exit 0)

### `.venv/bin/python -m build --wheel`

```
...
adding 'code_atlas_forge-0.1.0.dist-info/RECORD'
removing build/bdist.linux-x86_64/wheel
Successfully built code_atlas_forge-0.1.0-py3-none-any.whl
```

(exit 0)

### `cd explorer && npm run build`

```
Output location: /home/edgar/Documentos/projetos/forjas/code-atlas-forge/explorer/dist/explorer
```

(exit 0) — **com warnings** (verbatim):

```
[WARNING] Module 'graphology-layout-forceatlas2/helpers' used by 'src/app/features/graph/graph-canvas.component.ts' is not ESM
[WARNING] Module 'events' used by 'node_modules/sigma/dist/sigma.esm.js' is not ESM
[WARNING] Module 'graphology-utils/is-graph' used by 'node_modules/sigma/dist/sigma.esm.js' is not ESM
```

(CommonJS/AMD deps → optimization bailouts; bundle não tree-shakable nesses módulos.)

### `npx ng test --watch=false --browsers=ChromeHeadless` (em `explorer/`)

```
Chrome Headless 156.0.0.0 (Linux 0.0.0): Executed 219 of 219 SUCCESS (1.019 secs / 0.878 secs)
TOTAL: 219 SUCCESS
✔ Browser application bundle generation complete.
```

(exit 0)

### `npm audit --omit=dev` (em `explorer/`)

**exit 1** — 8 vulnerabilities (4 moderate, 4 high), verbatim do sumário:

```
@angular/common  <=19.2.25
Severity: high
Depends on vulnerable versions of @angular/core
Angular is Vulnerable to XSRF Token Leakage via Protocol-Relative URLs in Angular HTTP Client - https://github.com/advisories/GHSA-58c5-g7wp-6w37
@angular/common: Denial of Service (DoS) via OOM in Date Formatting (formatDate) - https://github.com/advisories/GHSA-48r7-hpm6-gfxm
@angular/common: Weak 32-Bit Cache Key Hashing in `HttpTransferCache` Leading to Cross-Request Data Leakage and State Poisoning - https://github.com/advisories/GHSA-39pv-4j6c-2g6v
@angular/common: Denial of Service (DoS) via OOM in Number Formatting (digitsInfo) - https://github.com/advisories/GHSA-p3vc-36g9-x9gr
@angular/common: Information Leak via Default Caching of Credentialed Requests in HttpTransferCache - https://github.com/advisories/GHSA-q6f4-qqrg-jv6x
Angular: Cache-Key Ambiguity in HttpTransferCache Leading to Cross-Request Response Reuse and State Poisoning - https://github.com/advisories/GHSA-jhpw-976m-542j
Angular: Information Leak via `HttpTransferCache` Bypass When Using `withRequestsMadeViaParent` - https://github.com/advisories/GHSA-p297-fm68-3q8c
@angular/common: XSRF Token Leakage via URL Parsing Discrepancy in Angular HTTP Client (Incomplete fix for CVE-2025-66035) - https://github.com/advisories/GHSA-jfgx-762w-99vr
fix available via `npm audit fix --force`
Will install @angular/common@22.2.3, which is a breaking change
```

```
@angular/core  <=19.2.25
Severity: high
Angular i18n vulnerable to Cross-Site Scripting - https://github.com/advisories/GHSA-prjf-86w9-mfqv
... (8 advisories — XSS/i18n/sanitization/hydration) ...
fix available via `npm audit fix --force`
Will install @angular/core@22.2.3, which is a breaking change
```

```
8 vulnerabilities (4 moderate, 4 high)

To address all issues (including breaking changes), run:
  npm audit fix --force
```

Versão instalada medida: `@angular/* 18.2.13` (dentro da faixa `<=19.2.25` afetada;
Angular 18 está EOL — fix exige bump de 3+ majors, breaking).

### CI do último commit de `main` — `gh run list -b main -L 3`

```
completed	success	archive cycle-7 specs (lf-0109..0126) — PR #12 merged	CI	main	push	40199628670	9m53s	2026-10-25T13:52:44Z
completed	cancelled	Cycle 7 — Domain intelligence, cross-domain resolution, federation & …	CI	main	push	40199608412	1m33s	2026-10-25T13:52:27Z
completed	success	archive cycle-6.1 specs (lf-0097..0108) — PR #11 merged	CI	main	push	40183313325	6m51s	2026-10-25T10:05:13Z
```

Último commit de `main` (`5c20177`, archive cycle-7): **CI success** em 9m53s.
O `cancelled` do meio é supersede pelo `concurrency.cancel-in-progress` do
`.github/workflows/ci.yml` (mesmo ref, push seguinte) — não é falha.

## Gaps confirmados

| # | gap | sev | evidência |
|---|-----|-----|-----------|
| 1 | Explorer preso em Angular 18.2.13 (EOL) com **8 vulnerabilidades de produção** (4 high, 4 moderate) — XSS sanitization bypass, i18n XSS, XSRF token leak, HttpTransferCache state poisoning. Fix só via `npm audit fix --force` → Angular 22.2.3 (breaking, 3+ majors). Mitigação parcial existente: API loopback-only + CSP restritivo (`server.py`), mas o Explorer renderiza dados de código indexado (superfície XSS real). | **P1** | `npm audit --omit=dev` exit 1 |
| 2 | Bundle do Explorer carrega deps CommonJS (`sigma`, `graphology-*`) → optimization bailouts, sem tree-shaking nesses módulos. | P2 | warnings do `npm run build` |
| 3 | `prompt_evo_cycle8.md` referenciado nas specs lf-0127/0128 **não existe** no repo (só `docs/prompt_evo_cycle2.md`, `cycle3.md`) — specs apontam prompt ausente. | P2 | `find . -name "prompt_evo*"` |
| 4 | Skip único na suite (`test_files_occurrences.py:162`) — cobertura de assertion declarada pendente. | P2 | pytest summary |

**Nenhum P0 encontrado** — lint, typecheck, testes Python (1188), testes Explorer
(219), wheel e CI de main estão todos verdes no baseline.

## NOT RUN (honesto)

- `pip audit` / CVE scan das deps Python — sem `pip-audit` instalado na `.venv`;
  deps de runtime são só `tree-sitter==0.25.2`, `tree-sitter-python==0.23.6`,
  `tree-sitter-typescript==0.23.2` (pinned, pyproject.toml).
- Bench de performance do backend — coberto pelo Phase 1
  (`lf-0128`, `benchmarks/realworld/`).
- Testes E2E Playwright do Explorer (`explorer/e2e/`) — fora dos comandos da
  spec; não executados neste Phase 0.
