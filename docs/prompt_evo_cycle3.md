# CODE ATLAS FORGE — CYCLE 3
## Semantic Resolution, Dependency-Aware Incremental Indexing & Snapshot Integrity

**Repository:** https://github.com/EdgarSocrates98/code-atlas-forge  
**Target:** Cycle 3 — Semantic & Incremental Intelligence  
**Reference branch:** `main`  
**Normative architecture:** `docs/BLUEPRINT-v1.md`  
**Previous cycle:** `docs/cycle-2-evidence.md`  
**Implementation language:** Python 3.12+  
**Documentation:** pt-BR

---

# 0. MISSÃO

Implemente o Cycle 3 integralmente, transformando o Code Atlas Forge em uma plataforma de inteligência semântica e indexação incremental confiável.

O resultado deve permitir que desenvolvedores e agentes identifiquem definições, referências e dependências com precisão, atualizando apenas os elementos afetados quando o código muda.

Você deve agir como um time de:

- Software Architects
- Compiler Engineers
- Static Analysis Engineers
- Python Engineers
- Graph Database Engineers
- Performance Engineers
- Security Engineers
- QA Engineers
- Agentic Engineering Specialists

Descubra e utilize as skills e agents disponíveis no ambiente quando agregarem valor. Não presuma que ferramentas estejam instaladas.

A implementação deve ser evidence-driven, offline-first, determinística e compatível com a arquitetura atual.

**Não inicie criando arquivos indiscriminadamente. Primeiro audite a main, compreenda os contratos, identifique riscos e registre um plano executável.**

---

# PHASE 0 — BASELINE & CYCLE 2 HARDENING

## 0.1 Auditoria

Inspecione:

- README, Blueprint, ADRs e roadmap.
- Parser adapters e ParseEngine.
- GraphBuilder e GraphValidation.
- IndexCoordinator.
- Persistência e migrations.
- StructuralSearch.
- Golden fixtures.
- Benchmarks.
- Segurança do scanner e leitura dos arquivos.
- GitHub Actions e PRs.

Registre o SHA da main e execute Ruff, mypy, pytest, instalação por wheel, smoke tests e benchmarks.

## 0.2 Corrigir integridade scan → parse

Verifique se os bytes analisados coincidem com os hashes do manifest.

Implemente verificação de conteúdo, proteção contra alterações concorrentes e prevenção de leitura de symlinks fora da raiz autorizada.

Não aceite um snapshot READY cuja identidade de conteúdo não corresponda ao código efetivamente analisado.

## 0.3 Corrigir evidências

Investigue `persist_graph()` e sua associação por índices.

Substitua mapeamentos baseados em posições compactadas por referências explícitas.

Teste evidências filtradas, arquivos ausentes e relações com múltiplas evidências.

Verifique se toda evidência referenciada existe, pertence ao snapshot correto e corresponde à origem da relação.

## 0.4 Corrigir consistência concorrente

Teste dois indexadores trabalhando simultaneamente, mudanças entre scan e commit, interrupções e recuperação após falha.

Garantir que somente uma publicação válida torne-se READY e que o último snapshot íntegro permaneça recuperável.

**Gate 0:** nenhum P1 aberto de integridade, segurança ou evidências.

---

# PHASE 1 — SEMANTIC RESOLUTION ARCHITECTURE

Criar um subsistema `resolvers/` independente de parsers, banco de dados e CLI.

Estrutura sugerida:

```text
src/code_atlas/resolvers/
  contracts.py
  registry.py
  coordinator.py
  capabilities.py
  scip/
    importer.py
    decoder.py
    mapper.py
    validation.py
  lsp/
    adapter.py
    lifecycle.py
    transport.py
  syntactic/
    resolver.py
```

Definir contratos versionados:

- `SemanticResolver`
- `ResolutionRequest`
- `ResolutionResult`
- `ResolvedReference`
- `ResolvedDefinition`
- `ResolutionDiagnostic`
- `ResolverCapability`
- `ResolverProvenance`

A arquitetura deve suportar diferentes níveis de precisão.

Ordem conceitual de evidência:

1. Índice semântico confiável, consistente com a revisão.
2. LSP com definição/referência confirmada.
3. Resolução sintática inequívoca.
4. Candidato heurístico.
5. Alvo ambíguo ou não resolvido.

Não utilizar rankings como prova automática de correção.

`confidence_class` e `resolution_status` precisam permanecer conceitos distintos.

**Gate 1:** interfaces testadas, desacopladas e com proveniência obrigatória.

---

# PHASE 2 — SCIP INTEGRATION

Implementar a importação e interpretação de índices SCIP.

Usar a especificação oficial:

https://github.com/sourcegraph/scip

Requisitos:

- Importar índices SCIP existentes.
- Interpretar documentos e ocorrências.
- Mapear identificadores de símbolos externos e locais.
- Correlacionar caminhos do índice com os arquivos do Atlas.
- Tratar definições e referências.
- Registrar linguagem, versão e indexador de origem.
- Preservar símbolos não encontrados.
- Tratar índices incompatíveis, incompletos e corrompidos.
- Validar identidade da revisão e do workspace.
- Impedir associações falsas entre repositórios ou commits.

Não presumir que SCIP usa os mesmos IDs internos de `LogicalSymbol`.

Criar uma camada explícita de correlação.

Não exigir que o Atlas execute compiladores ou builds para consumir SCIP.

O uso de indexadores externos deve ser opt-in, com políticas de segurança e limites.

Implementar testes com índices reais ou fixtures protobuf válidas e geradas por ferramentas oficiais.

**Gate 2:** navegação definition/reference baseada em evidências SCIP reais.

---

# PHASE 3 — OPTIONAL LSP RESOLVERS

Implementar adaptadores LSP opcionais.

Referência:

https://microsoft.github.io/language-server-protocol/

Suportar, quando o servidor declarar a capacidade:

- `textDocument/definition`
- `textDocument/references`
- `textDocument/documentSymbol`
- `workspace/symbol`

Não assumir suporte uniforme entre servidores.

Criar mecanismo de lifecycle:

- Inicialização
- Negotiation de capabilities
- Timeout
- Cancelamento
- Shutdown
- Tratamento de falhas
- Limites de memória e processos

Não instalar ou executar servidores LSP automaticamente.

A ativação deve ser explícita, configurável e documentada.

Não permitir execução de comandos arbitrários sugeridos por um repositório.

Diferenciar LSP de SCIP no registro de proveniência.

**Gate 3:** adaptador funcional em ambiente controlado, com fallback seguro quando ausente.

---

# PHASE 4 — DEPENDENCY-AWARE INCREMENTAL ENGINE

Esta é uma das prioridades principais do ciclo.

Criar um mecanismo incremental que compare o manifest atual com o último snapshot READY.

Classificar alterações:

- ADDED
- MODIFIED
- DELETED
- RENAMED
- UNCHANGED
- ANALYZER_CHANGED
- RESOLVER_CHANGED

A distinção entre rename e delete+add deve ser baseada em evidências, não inferência arbitrária.

## Planejamento de invalidação

Construir um `InvalidationPlanner` que determine:

- Arquivos que exigem novo parsing.
- Símbolos alterados.
- Módulos afetados.
- Imports dependentes.
- Relações que precisam de reavaliação.
- Resultados estruturais reutilizáveis.
- Resultados semânticos invalidados.
- Componentes que podem permanecer intactos.

Implementar estratégias conservadoras para dependências incompletas.

Se o motor não puder comprovar que determinada parte permanece válida, deve reavaliá-la.

Evitar tanto recomputação excessiva quanto reutilização incorreta.

## Estrutura proposta

```text
src/code_atlas/indexing/
  change_detector.py
  invalidation.py
  dependency_planner.py
  cache.py
  reuse.py
  scheduler.py
  coordinator.py
```

Separar planejamento da execução efetiva.

Fornecer uma representação consultável do plano de indexação.

O plano deve informar quantos arquivos serão parseados, reutilizados e invalidados, bem como as justificativas.

**Gate 4:** alterações locais não exigem reparse integral quando não há dependência que o justifique.

---

# PHASE 5 — SNAPSHOT REUSE & TRANSACTIONAL BUILDS

Implementar reutilização segura de resultados.

A estratégia deve considerar:

- Hash do conteúdo.
- Linguagem.
- Versão da gramática.
- Versão do parser.
- Versão do extrator.
- Versão do resolver.
- Contratos de graph builder.
- Dependências relevantes.
- Configuração efetiva.

Não reutilizar um resultado somente porque o caminho do arquivo permaneceu igual.

Não reutilizar referências resolvidas quando seus alvos ou condições de resolução mudaram.

Avaliar cache de ParseResult ou representações intermediárias serializáveis, com versões e verificações de integridade.

O snapshot novo deve preservar isolamento lógico em relação ao anterior, mesmo quando dados físicos forem compartilhados.

Garantir publicação transacional, rollback, recuperação e garbage collection seguro.

Executar testes de corrupção de cache e mudanças de versão.

**Gate 5:** reutilização comprovada sem inconsistências, com snapshot antigo preservado diante de falhas.

---

# PHASE 6 — GIT-AWARE CHANGE INTELLIGENCE

Implementar tratamento explícito para:

- Arquivos renomeados.
- Arquivos removidos.
- Branch switch.
- Detached HEAD.
- Worktrees.
- Mudanças não commitadas.
- Mudanças simultâneas.
- Arquivos ignorados.
- Repositórios sem Git.
- Caminhos case-insensitive.
- Renomeações apenas de caixa.
- Imports reexportados.
- Dependências removidas.

Não exigir Git para funcionamento básico.

A identidade lógica do repositório deve ser tratada separadamente do caminho físico.

Avaliar e documentar estratégia para relocação de workspace e correlação de repositórios.

Ao alternar branches, impedir que resoluções semânticas de uma revisão sejam apresentadas como válidas em outra.

**Gate 6:** golden tests para rename, delete, dirty worktree e branch switch.

---

# PHASE 7 — SEMANTIC CLI & QUERY INTERFACES

Adicionar comandos ou ampliar os existentes, mantendo compatibilidade:

```bash
code-atlas index . --incremental --json

code-atlas index . --full --json

code-atlas references <symbol-id> --json

code-atlas definitions <symbol-id> --json

code-atlas dependencies <symbol-id> --json

code-atlas index-plan . --json
```

A interface pode ser ajustada após análise da CLI existente, mas mudanças precisam ser justificadas e documentadas.

O resultado JSON deve incluir:

- Snapshot
- Repository
- Query
- Results
- Resolution status
- Confidence class
- Resolver
- Evidence
- Truncation status
- Diagnostics

Não incluir uma referência ambígua na categoria de resolvida.

Limitar profundidade de travessia, número de resultados e custo.

Consultas devem funcionar sem LLM.

**Gate 7:** CLI funcional, dados reais, paginação e contratos estáveis.

---

# PHASE 8 — PERFORMANCE ENGINEERING

Realizar profiling real de:

- Discovery
- Hashing
- Parsing
- GraphBuilder
- Semantic resolution
- Cache lookup
- Invalidation planning
- SQLite transactions
- Índices
- Queries

O benchmark do Cycle 2 apontou aproximadamente 20,4 segundos de residual de persistência/coordenação em 5.000 arquivos.

Investigar esse gargalo.

Avaliar:

- `executemany`
- Inserções em lotes
- Índices adequados
- Reutilização de linhas imutáveis
- Prepared statements
- Custos de FKs
- WAL checkpoints
- Cache de parse
- Redução de scans redundantes
- Estratégias de escrita e isolamento
- Retention/GC

Não desabilitar verificações de integridade para melhorar benchmarks.

Comparar:

1. Cold index
2. No-op
3. Alteração de um arquivo isolado
4. Alteração de um módulo importado
5. Renomeação
6. Exclusão
7. Mudança de parser
8. Mudança de resolver
9. Full rebuild

Usar corpora com 100, 1.000, 5.000 e 10.000 arquivos quando o ambiente permitir.

Medir tempo total, p50/p95, CPU, RSS, tamanho do banco e quantidade de trabalho reaproveitado.

**Gate 8:** demonstrar economia de processamento sem perda de precisão, com resultados reproduzíveis.

---

# PHASE 9 — SEMANTIC EVALUATIONS

Criar fixtures e datasets ground truth para Python, TypeScript e TSX.

Cobrir:

- Import aliases
- Python relative imports
- Reexports
- TypeScript barrel exports
- Namespaces
- Herança
- Interfaces
- Overloads
- Símbolos homônimos
- Imports externos
- Star imports
- Chamadas dinâmicas
- Referências ambíguas
- Circular imports
- Código incompleto
- Índices SCIP incompatíveis
- LSP indisponível
- Mudanças de branch
- Arquivos renomeados
- Alvos excluídos

Medir separadamente:

- Definition precision
- Definition recall
- Reference precision
- Reference recall
- Resolved coverage
- Ambiguous rate
- Unresolved rate
- False-positive rate
- Invalidation correctness
- Reuse correctness

Não combinar resultados de métodos semânticos e heurísticos como se tivessem a mesma confiabilidade.

Criar fixtures capazes de detectar falsos vínculos aparentemente plausíveis.

**Gate 9:** resultados comparáveis, auditáveis e com ground truth explícito.

---

# PHASE 10 — SECURITY, PORTABILITY & OBSERVABILITY

Garantir:

- Operação offline por padrão.
- Ausência de telemetria não autorizada.
- Sem execução de código dos repositórios indexados.
- Sem download automático de servidores LSP.
- Sanitização de caminhos.
- Proteção contra symlinks e path traversal.
- Limites para índices externos.
- Proteção contra arquivos maliciosos e payloads enormes.
- Timeouts e cancelamento.
- Logging com redaction.
- Falhas registradas sem vazar segredos.
- Compatibilidade Linux/Windows.

Adicionar métricas locais de operação, sem transmissão externa.

Validar a matriz de CI e revisar licenças das novas dependências.

**Gate 10:** nenhuma regressão de segurança, instalação ou portabilidade.

---

# PHASE 11 — DOCUMENTATION & INTEGRATION READINESS

Atualizar documentos existentes e criar:

- `docs/cycle-3-architecture.md`
- `docs/cycle-3-evidence.md`
- `docs/cycle-3-benchmarks.md`
- `docs/semantic-resolution.md`
- `docs/scip-integration.md`
- `docs/lsp-adapters.md`
- `docs/incremental-indexing.md`
- `docs/cache-and-invalidation.md`
- `docs/snapshot-consistency.md`

Registrar ADRs novos para decisões que alterem arquitetura, persistência ou contratos.

Atualizar o roadmap e a capability matrix.

Preparar o núcleo para o Cycle 4, mas não implementar prematuramente MCP, embeddings, Context Packs ou frontend Angular.

---

# PHASE 12 — HOMOLOGAÇÃO FINAL

Executar validação integral:

1. Ruff.
2. mypy strict.
3. pytest completo.
4. Golden corpus.
5. E2E da CLI.
6. Migrações.
7. Integridade de snapshots.
8. Concorrência.
9. Testes de segurança.
10. Instalação via wheel.
11. Linux/Windows CI.
12. Benchmarks.
13. Regression suite do Cycle 1.
14. Regression suite do Cycle 2.
15. Avaliação de precisão semântica.
16. Testes de invalidação incremental.
17. Testes de rollback e recuperação.

Não reduzir ou remover testes para obter CI verde.

Não aceitar fallback silencioso que transforme falhas reais em sucesso.

## Critérios finais

O Cycle 3 estará pronto quando:

- Referências semânticas forem resolvidas por evidência verificável.
- SCIP estiver integrado com índices reais.
- LSP opcional tiver lifecycle controlado.
- Atualização incremental funcionar.
- Mudanças de dependência invalidarem resultados afetados.
- Reparse desnecessário for evitado.
- Snapshots forem transacionalmente íntegros.
- Renomeações, exclusões e branches estiverem testados.
- Evidências nunca apontarem para conteúdo ou revisão incorretos.
- Testes e benchmarks demonstrarem o comportamento.
- CI estiver verde nas plataformas suportadas.
- Nenhum P0/P1 de integridade ou segurança permanecer aberto.

## Entrega final

Produza relatório contendo:

- SHA inicial e final.
- PRs e commits.
- Arquitetura entregue.
- Contratos.
- Migrations.
- Indexadores e adaptadores.
- Métricas de precisão e recall.
- Benchmarks antes/depois.
- Testes executados.
- Gates PASS/FAIL/NOT RUN.
- Gaps remanescentes classificados P0/P1/P2.
- Limitações.
- Readiness para Cycle 4.

Crie uma branch dedicada `cycle-3`, abra PR e somente realize merge após os gates críticos estarem aprovados.

**Resultado esperado:** Code Atlas Forge passa a compreender referências semânticas verificáveis e atualizar seus grafos de maneira incremental, segura e economicamente eficiente.

---

## 5. Evolução esperada

| Indicador | Cycle 2 documentado | Meta do Cycle 3 |
|---|---|---|
| Indexação de 5 mil arquivos | 26,5 s | Reduzir custo após profiling |
| No-op de 1 mil arquivos | 0,12 s | Manter ou melhorar |
| Alteração de um arquivo | Reprocessamento estrutural amplo | Reutilização incremental |
| Referências | Predominantemente sintáticas | SCIP/LSP quando disponíveis |
| Mudanças de branch | Identificação básica | Invalidação correta |
| Evidência semântica | Limitada | Referência verificável à revisão |
| Recuperação após falha | Snapshot anterior preservado | Recuperação + cache consistente |

Não estabeleceria uma meta artificial de "10 vezes mais rápido" antes de medir um baseline incremental. O ganho mais importante será evitar reconstruções inteiras quando uma alteração afetar apenas uma pequena parte do código.

## 6. Por que esse Cycle 3 é decisivo

É esse ciclo que permitirá que o Atlas entregue respostas melhores às Forjas.

Por exemplo, ao modificar uma função de transformação PySpark, o Code Atlas poderá identificar quais referências foram comprovadas por um índice semântico, quais dependem apenas da análise sintática e quais continuam sem resolução.

No Cycle 4, essas descobertas poderão alimentar Context Packs e ferramentas MCP. No Cycle 5, serão a base para uma análise de impacto mais confiável.

**Recomendo executar o Cycle 3 exatamente nessa ordem: integridade primeiro, resolução semântica depois e, por último, otimização incremental.** Um motor rápido que reutiliza relações erradas comprometeria toda a proposta do Code Atlas.

A auditoria desta rodada foi de código e documentação disponíveis na `main`; não executei a suíte de testes em um checkout local. A etapa de homologação independente continua sendo necessária antes de considerar o novo ciclo concluído.