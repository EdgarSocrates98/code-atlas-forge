# Cycle 8.1 — architecture notes

No architectural changes this cycle — hardening and packaging only.
The canonical architecture remains:

- **Deterministic pipeline**: discovery/scanning → Tree-sitter parse →
  syntactic resolution (optional SCIP/LSP adapters) → typed graph with
  provenance → SQLite snapshot store (schema v12, FTS5).
- **Local-first**: no Docker, SaaS, embeddings requirement, Neo4j, API
  keys, or Forge components. Indexed code is never executed.
- **Domain adapters** (SQL, PySpark, AWS Data, API, Terraform, CI/CD)
  feed `domain_entities`/`domain_relations` with canonical identity
  (catalog/namespace/environment) and honest uncertainty
  (`possible` never promoted to `verified`).
- **Federation**: multi-repo views pinned by snapshot, `view_id` v2
  (membership + identity keys + pins + analyzer versions + schema).
- **Retrieval**: lexical FTS5 + bounded graph + RRF fusion, optional
  vectors; context packs; MCP stdio.
- **Explorer**: Angular 22 SPA (Sigma/Graphology, ForceAtlas2 in a Web
  Worker with chunked fallback), read-only, evidence-first; shipped
  inside the wheel (`code_atlas/explorer_dist/`).
- **Serving**: stdlib HTTP server, loopback, strict CSP
  (`script-src 'self'`, `worker-src 'self'`, `object-src 'none'`).

ADRs: `docs/adr/` (index up to date — ADR-0010 domain identity was the
last structural decision; nothing in 8.1 warranted a new ADR).
