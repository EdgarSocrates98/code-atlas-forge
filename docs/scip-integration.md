# Integração SCIP (opt-in)

O Atlas importa índices [SCIP](https://github.com/sourcegraph/scip)
**pré-gerados** — nunca executa o indexer externo, nunca baixa nada.

## Pipeline

```
load_index(path)                    # só path — cap MAX_INDEX_BYTES
  → decoder protobuf stdlib         # wire-reader próprio, zero deps
  → importer → ScipIndex tipado     # documents/occurrences/symbols
  → validation                      # paths seguros, duplicatas, caps
  → revision binding                # project_root ≡ workspace_root
  → ScipResolver                    # resolve por span → nid do Atlas
```

## Segurança

- Entrada é **somente um caminho** — conteúdo tratado como untrusted.
- `MAX_INDEX_BYTES` default; `max_bytes` configurável — oversize
  rejeitado antes de decodificar.
- Bytes malformados/truncados → `LoadOutcome.errors` (nunca raise
  para input externo).
- `project_root` divergente do workspace → rejeitado; override só via
  `allow_revision_mismatch=True` (opt-in explícito).
- Decoder é stdlib-only — nenhum `import protobuf` (teste auditado).

## Correlação

Símbolos SCIP (`scip-python . mypkg 0.1.0 \`pkg/util.py\`/helper().`)
são parseados em descriptor+doc-path+membros; ocorrências por span
(0-based → 1-based) correlacionam com nós do grafo via `ScopeUniverse`
compartilhado pelo builder. O que não correlaciona → `external`,
nunca `resolved` inventado.

## Proveniência e revision safety (lf-0028)

`assess_revision()` produz `IndexProvenance` inspecionável por índice:
`index_format`, `index_sha256`, `producer{name,version}`,
`repository_identity`, `worktree_digest` (digest dos arquivos
indexados vistos agora), `indexed_at` (mtime do .scip — evidência
host, marcada), `validation_status` e `validation_evidence`.

Verificações: project_root ↔ workspace; arquivos indexados ausentes
ou mais novos que o índice (stale); paths não-normalizados;
ocorrências além do fim do arquivo atual; `position_encoding` fora
de {0, UTF-32}; metadata sem tool version; símbolos definidos em dois
documentos. `source_revision` fica vazio — SCIP metadata não prova
commit; ausente nunca é preenchido artificialmente.

- `current` → resolução normal.
- `degraded` → resolver resolve, mas confidence capada em
  `heuristic` e detail marcado `[degraded]` — relação degradada
  nunca vira comprovada.
- `rejected` → nenhuma resolução; `index-rejected` diagnostic
  (error). Índice incompatível nunca contamina snapshot READY.
- Sem assessment explícito, o resolver assume `degraded` — índice
  não-verificado nunca é tratado como provado por default.
