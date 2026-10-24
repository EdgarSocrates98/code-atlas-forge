# Auditoria de Baseline — Cycle 5 (lf-0058)

Baseline: `100974c` (main pós Cycle 4.1). 501 testes, ruff clean,
mypy strict clean (87 arquivos).

## Achados corrigidos

### P1 — Paginação híbrida ignorava limit/cursor (`search/engine.py`)

`_hybrid_hits` fundia os rankings e devolvia **todos** os candidatos;
`SearchResult` reportava `truncated=False`, sem `next_cursor`, sem
`offset` aplicado. Um `limit=10` podia retornar 26 itens.

**Fix**: fatia `fused[offset:offset+lim]` após fusão, `truncated`
calculado sobre a lista fundida, `next_cursor` via `encode_cursor`
ligado ao fingerprint de query+filtros+snapshot. `lim` clampado em
`[1, 200]`.

### P1 — Cursor vazava para os sub-canais

`dataclasses.replace(req, limit=fetch)` mantinha `req.cursor` —
sub-canal que o aplicasse mudava a lista fundida entre páginas
(medido: item `pkg.inventory` aparecia nas páginas 1 e 2). Fix:
`cursor=None` no fan-out; a lista fundida é idêntica entre calls.

### P1 — Cursor não era ligado a snapshot

Fingerprint cobria só query+language+kind+path — cursor de uma
revisão anterior continuava válido após reindex. Fix: `snapshot` no
dict do fingerprint → cursor de outra revisão → `invalid-cursor`.

### P2 — `EvidenceValidator` não revalidava metadados (refs.py)

`validate_occurrence` checava existência + path + span_start;
`validate_relation` checava só `from_id`. Metadados adulterados
(qualified_name, symbol_id, span_end, content_hash, parse_status,
to_id, type, resolution_status, confidence_class, resolver) passavam
com `ok=True`.

**Fix**: política "None na ref = não assertado; declarado e divergente
→ `metadata-mismatch`". Novos validadores: `validate_symbol`,
`validate_source_file`, `validate_snapshot`. 13 casos de adulteração
testados.

### P2 — Colisão de identidade em monorepo (identity.py)

`identity_key` = remote URL → dois projetos distintos sob o mesmo
remote (raiz indexada abaixo do toplevel) **colidiam num único
`repositories` row** — observado no corpus vendored do lf-0055.

**Fix**: `GitInfo.toplevel` (`rev-parse --show-toplevel`) + sufixo
`@<relpath>` quando a raiz indexada é sub-diretório do repo:
`git-remote:<url>@apps/web`. Raiz == toplevel preserva o formato
legado (retrocompatível); realocação preserva a chave (relpath
estável); fallback `git-root:<sha>@sub` idem.

## Decisões registradas

- Identidade = *repo git + escopo de raiz*, não path absoluto
  (relocação continua suportada).
- `total` não adicionado a `SearchResult`: contagem fundida exata já é
  conhecida (`len(fused)`), mas expor `total` exigiria bump de schema —
  adiado para quando a Phase de contratos do Impact Engine versionar o
  envelope. `truncated` + `next_cursor` cobrem o uso.

## Verificação

- `pytest` — 501 passando (era 475 no baseline; +26 testes novos)
- `ruff check` — clean; `mypy src` — clean
- Novos: `test_hybrid_pagination` (7), `test_evidence_tamper` (13),
  `test_identity_scope` (6)
