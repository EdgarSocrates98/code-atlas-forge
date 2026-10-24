# Historical Impact Reconciliation

Consumidores de símbolos REMOVIDOS navegam arestas do snapshot base.
Cada impacto recebe `consumer_state`:

- `still_present` — consumidor existe no destino (risco real)
- `consumer_modified` — consumidor também consta como alterado
- `also_removed` — consumidor removido junto (prioridade menor)
- `unresolved_in_target` — sem ocorrência no destino, verificação
  inconclusiva

Existência histórica ≠ risco atual; o classificador de risco e os
relatórios distinguem os estados via campo aditivo `consumer_state`
(retrocompatível, default "").
