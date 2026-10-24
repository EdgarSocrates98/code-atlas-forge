# Evidence Grounding (Phase 4)

`search/refs.py`:

- `OccurrenceRef(repository_id, snapshot_id, occurrence_id, symbol_id,
  qualified_name, source_path, span, content_hash, parse_status)` —
  validada contra SQLite (existência + dono real do snapshot).
- `RelationRef` idem para arestas.
- `EvidenceValidator`/`EvidenceValidationResult`: cross-repo e
  cross-snapshot **rejeitados**; ausência explícita, nunca fabricada.

Integração:

- SearchHits carregam `occurrence_id` real (structural corrigido via
  subqueries escalares) + `evidence=occ:{id}`.
- `atlas_evidence`: refs canônicas por ocorrência do símbolo pedido;
  evidência de arquivo escopada às ocorrências — não o snapshot todo.
