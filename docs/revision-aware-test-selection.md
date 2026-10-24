# Revision-Aware Test Selection

`TestSelector.select(conn, target_sid, changes, impacts,
base_snapshot_id=base_sid)`:

1. arestas teste→símbolo alterado no **alvo** → `direct`, `revision="target"`
2. arestas históricas na **base** para símbolos removidos → `direct`,
   `revision="base"` — testes que referenciavam o contrato removido
3. impact candidates em arquivos de teste → `direct`/`transitive`,
   revision derivada de `consumer_state`
4. convenção de nome → `heuristic`, nunca cobertura

Ausência de candidatos não é evidência de segurança.
