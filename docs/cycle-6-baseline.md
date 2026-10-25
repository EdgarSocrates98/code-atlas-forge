# Cycle 6 — Baseline Audit (lf-0083)

Baseline verificada em main `e2a7ed7` (pós Cycle 5.1).

## Estado dos componentes

| componente | estado | observação |
|---|---|---|
| ImpactEngine + RevisionPlan | OK | `semantic-blocked` em mismatch/unavailable; consumer_state aditivo |
| SnapshotResolver | OK | repo-scoped, READY-only, cross-repo rejeitado |
| SymbolDiff/BlastRadius | OK | hash 1:1, multi-origem `(occ,origem)`, base-snapshot p/ removidos |
| EvidenceValidator | OK | metadata-mismatch, validate_symbol/source_file/snapshot |
| SearchEngine | OK | paginação híbrida estável (cursor não vaza) |
| ContextEngine | OK | budget estrito, critical-omitted declarado |
| MCP | OK | E2E real SDK 2.3.0, 11 tools |
| SQLite Store | OK | WAL, transações, migrations vN |

## Achados do Cycle 6.0

1. **Canal vetorial escrevia em conexão read-only** → crash 500 na API.
   Fix: `PRAGMA query_only` detectado → diagnóstico
   `vector-unavailable-readonly` (degradação declarada).
2. **Guards de segurança Cycle 3.1 proibiam `import http` em `src/`** —
   fronteira HTTP deliberada criada em `api/`; guards refinados com
   exceção documentada (testes continuam cobrindo o resto de `src/`).
3. **SQL-audit guard reprovou IN-lists** — adotado padrão `json_each(?)`
   e queries constantes nomeadas (mesma convenção do store).
4. **Teste de help proibia verbo `serve`** — help do `explorer`
   reescrito para não vazar verbos-futuro.

## Gates

- 539 testes Python (+14 api), ruff clean, mypy strict (103 arquivos)
- Angular 18.2.13: `ng build` prod OK (257.6 kB inicial, lazy chunks),
  `ng test` 13/13 em ChromeHeadless real
- `code-atlas explorer` serve SPA + API verificado E2E local
- npm audit: 55 vulnerabilidades em deps transitivas de dev do
  toolchain Angular 18 (esbuild/vite/webpack-dev) — rastreado;
  runtime deps limpas; sem `npm audit fix --force` (breaking).
