# Context Engineering (lf-0040/41)

`code-atlas context --task "q" --level orientation|focus|deep
--budget N` → `ContextPack` versionado: items priorizados (direto,
vizinho, arquivo, dependência, evidência), dedup por `item_id`,
reservas de budget (ADR-0027), `unresolved_refs` + `expansion_refs`
para disclosure progressivo.

`code-atlas context-expand --symbol X --delivered id...` continua no
**mesmo snapshot** — snapshot removido/failed = erro explícito, nunca
fallback silencioso. Renderers: `--format json|markdown|human`.
