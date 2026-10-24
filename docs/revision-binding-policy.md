# Revision Binding Policy

| Binding | Política |
|---|---|
| exact | análise semântica permitida |
| content_verified | permitida — só com manifest-sha256 comprovado (snapshot.worktree_digest == scan atual, revisão=HEAD, worktree limpo) |
| partial | análise marcada `partial-analysis`; nunca apresentada como correspondente à revisão |
| mismatch | **bloqueia** análise semântica (`semantic-blocked`); ChangeSet Git-only preservado |
| unavailable | falha tipada `revision-unresolved` |

Snapshot ausente para commit pedido → `snapshot-for-revision-missing`
com comando seguro de index; nunca substituição silenciosa, nunca
checkout, nunca newest-READY-como-histórico.
