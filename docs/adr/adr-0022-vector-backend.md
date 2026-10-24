# ADR-0022 — Backend de busca vetorial: sqlite-vec opt-in + bruteforce stdlib

**Status:** aceito (lf-0039) · **Decisores:** Cycle 4 Grill Gate

## Contexto

Hybrid search (lf-0038) precisa de um quarto ranking — vetorial — sem
quebrar local-first, offline-first e instalação mínima (`pip install
code-atlas-forge` não pode puxar dependências pesadas nem exigir
servidor). Candidatos avaliados:

| backend | licença | portabilidade | deps | veredito |
|---|---|---|---|---|
| **sqlite-vec** | MIT/Apache-2.0 | extensão C single-file (linux/mac/win) | +1 pacote | **extra `[vector]`** |
| FAISS | MIT | wheels x86/ARM; build nativo | numpy+faiss (~100MB) | rejeitado: peso |
| LanceDB | Apache-2.0 | wheels; runtime Rust | lancedb+pyarrow | rejeitado: peso |
| pgvector | PostgreSQL | exige servidor | postgres + driver | rejeitado: SaaS-like |
| Chroma | Apache-2.0 | servidor ou embedded pesado | muitas | rejeitado: peso |
| **bruteforce** | — | puro stdlib | 0 | **fallback default** |

## Decisão

1. **Dupla implementação** sobre a mesma tabela `vector_embeddings`
   (migração v8): `SqliteVecIndex` (vec0 KNN) quando `sqlite_vec`
   importável; `BruteForceIndex` (scan O(n·dim) com filtro de
   snapshot) sempre disponível — instalação mínima fica funcional.
2. **Provider default local**: `NaiveEmbeddingProvider` — feature
   hashing de tokens (sha256→bucket, sinal, L2-norm, dim=256).
   Determinístico, zero egress. Documentado como baseline NÃO
   semântico: capta sobreposição léxica, não sinônimos.
3. **Seam `EmbeddingProvider`**: provider externo (API/local server)
   só via opt-in explícito de configuração — nunca envia código a
   terceiros por default.
4. **Cache por conteúdo** (`embedding_cache`, PK model+version+hash):
   rebuild só quando conteúdo OU modelo mudam. `model_version` muda →
   linhas antigas não casam → reindex honesto.
5. **Isolamento por snapshot**: `snapshot_id` é filtro estrutural no
   índice, nunca pós-query; `ON DELETE CASCADE` limpa com o snapshot.

## Consequências

- Instalação mínima: +0 dependências; vetorial funciona (lento em
  bases grandes — O(n·dim) declarado).
- `pip install code-atlas-forge[vector]`: KNN nativo.
- `capabilities()` reporta backend real (`sqlite-vec|bruteforce`) e
  provider — honestidade de capability.
