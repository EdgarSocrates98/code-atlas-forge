# ADR-0021 — Política de precedência de evidência (verificação por tier superior)

## Contexto

Até o Cycle 3.1, uma aresta `resolved` pelo syntactic resolver era
fechada: o `SemanticEnricher` retornava a aresta intacta sem consultar
resolvers de tier superior (SCIP tier 1, LSP tier 2). Isso viola a
ordem de precedência do prompt Cycle 4 §0.2 — resolução sintática não
pode bloquear verificação por evidência semântica confirmada — mas a
regra inversa também vale: evidência externa não pode sobrescrever uma
relação válida sem revisão/origem/integridade comprovadas.

## Decisão

`SemanticEnricher` passa a ter dois caminhos por aresta elegível:

1. **Não-resolvida** — comportamento inalterado (cadeia completa).
2. **`resolved`** — *verificação*: consulta-se `ResolverCoordinator`
   com `above_tier=<tier atual da aresta>` (somente resolvers
   estritamente superiores). Desfechos determinísticos:
   - **Sem resolver superior configurado** → aresta intacta, custo zero.
   - **Mesmo alvo confirmado** → `semantic_confirmed`: proveniência sobe
     (`resolver`, `confidence_class` do tier superior) + `EvidenceRecord`
     `validation_status="confirmed"`.
   - **Alvo divergente persistível** → `ambiguous` + `EvidenceRecord`
     `validation_status="conflict"` (kept/alternative registrados) +
     diagnóstico `semantic-conflict`. O `to_id` original é preservado —
     nunca sobrescrito silenciosamente.
   - **Não-resolvido / alvo fora do snapshot** → aresta intacta.

O tier atual da aresta é inferido do label persistido: `scip`/`resolver:scip*`
→ SEMANTIC_INDEX, `lsp:*` → LSP, demais → SYNTACTIC. Resolver desconhecido
é tratado como sintático (conservador).

Stats novas: `semantic_verified`, `semantic_confirmed`,
`semantic_conflicts` (somadas às existentes).

## Consequências

- Positivo: evidência confirmada por tier superior fica auditável na
  tabela `evidence` (colunas da migração v6); conflitos ficam
  explicitamente `ambiguous` em vez de silenciosos.
- Custo: zero quando nenhum resolver externo está habilitado (o caso
  default); opt-in por configuração.
- Restrição: verificação depende do `to_text` original — arestas sem
  texto de origem preservado não são verificadas (caso raro; documentado).
