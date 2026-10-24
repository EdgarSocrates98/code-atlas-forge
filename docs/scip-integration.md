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
