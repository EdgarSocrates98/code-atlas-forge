# Code Atlas Forge — Technical Blueprint v1.0

**Status:** Baseline de design — Cycles 1, 2, 3 e 3.1 (semantic
hardening) entregues; ver `docs/roadmap.md` e `docs/cycle-3.1-evidence.md`
**Date:** 2026-10-24  
**Language:** pt-BR (contracts and code identifiers in English)

## 1. Missão

Criar uma plataforma open source, local-first, de inteligência estrutural, semântica e arquitetural de software, com grafos tipados, proveniência verificável, indexação incremental, contexto econômico para agentes e visualização navegável. O Code Atlas opera autonomamente e oferece integrações opcionais ao Forge Ecosystem.

### Não objetivos
- Não substituir IDE/LSP, ferramentas de segurança, Forge Doctor, Forge Lens ou The Forge.
- Não usar LLM como fonte primária de fatos estruturais.
- Não executar scripts do repositório indexado por padrão.
- Não exigir Docker, conta SaaS, embeddings, Neo4j, API key ou qualquer Forge para operações locais.
- Não tentar suportar plenamente todas as linguagens no primeiro ciclo.

## 2. Restrições arquiteturais

- Python 3.12+ como alvo inicial, com verificação de matrizes Windows/Linux; frontend Angular somente após núcleo e API estabilizados.
- Apache-2.0 como licença proposta, sujeita a auditoria de licenças e marcas.
- Arquitetura hexagonal/modular: `contracts`, `discovery`, `parsers`, `resolvers`, `graph`, `indexing`, `storage`, `search`, `impact`, `context`, `api`, `mcp`, `observability`, `security`.
- Dependências fluem de adaptadores para contratos de domínio. O domínio não importa CLI, web framework, MCP, UI, bancos externos, nem as demais Forjas.
- Operação offline por padrão; qualquer integração remota exige opt-in explícito.
- Sem dependência circular entre pacotes nem acoplamento compulsório com ecossistema Forge.

## 3. ADRs

| ID | Decisão | Consequências e critérios |
|---|---|---|
| ADR-001 | local-first e offline-first | Não requer serviços externos; egress desligado por padrão |
| ADR-002 | monorepo modular em Python | Ports/adapters, contratos estáveis e pacotes testáveis |
| ADR-003 | parsing via Tree-sitter | AST incremental; não confundir sintaxe com resolução semântica |
| ADR-004 | SCIP/LSP por adaptador | References/definitions com capacidade declarada, fallback sinalizado |
| ADR-005 | graph tipado e versionado | Identidade lógica separada de ocorrência em snapshot |
| ADR-006 | SQLite WAL + FTS5 | Migrações versionadas, consultas parametrizadas, único writer coordenado |
| ADR-007 | incremental + snapshots | Atomicidade e invalidation de dependências; último snapshot bom permanece consultável |
| ADR-008 | context packs orçamentados | Seleção de evidências com limites e redaction; não enviar tudo ao LLM |
| ADR-009 | API REST + MCP | Contratos versionados, paginação, schema validation, autorização para remoto |
| ADR-010 | Angular + Sigma.js | Explorer progressivo, agregação e renderização sob demanda |
| ADR-011 | federação por contratos | Integrações Forge opt-in e testadas independentemente |
| ADR-012 | evidence-first | Toda relação tem origem, extrator, versão, confiança e status de resolução |
| ADR-013 | configuração e segredos | XDG/AppData, configs explícitas, credenciais nunca armazenadas em grafo/logs |
| ADR-014 | segurança de conteúdo | Repositório indexado é untrusted; não seguir symlinks fora da raiz; não executar código |

## 4. Bounded contexts e responsabilidades

1. `discovery`: raiz do workspace, Git metadata, ignores, linguagens, fingerprints, limite de escaneamento.
2. `parsers`: AST, símbolos, imports, exports; adaptadores com capability matrix.
3. `resolvers`: SCIP/LSP por linguagem; proveniência de símbolo resolvido vs candidato.
4. `graph`: tipos de nó/aresta, identidade, topologia, invariantes, projeções.
5. `indexing`: diff de arquivos, fila de invalidation, staging, transactions, snapshots.
6. `storage`: SQLite schema/migrations, FTS, índices e query plans.
7. `search`: lexical/structural, embeddings opcionais, reranking com baseline.
8. `impact`: transitive dependency traversal e incerteza/risco sem números artificiais.
9. `context`: pacotes para tarefas com budget e referências recuperáveis.
10. `api`/`mcp`: interfaces não invasivas ao domínio.
11. `explorer`: frontend independente, acessibilidade e proteção contra grafos gigantes.
12. `federation`: registry de repositórios, correlação entre índices e interoperabilidade Forge.

## 5. Modelo canônico

### Entidades
- `Repository`: id, sanitized origin, root handle, git provider, default branch.
- `Snapshot`: id, repository_id, git_ref, commit_sha nullable, worktree_digest, analyzer_versions, created_at, status (`BUILDING`, `READY`, `FAILED`, `STALE`), parent_snapshot_id.
- `SourceFile`: id, repo_id, normalized relative POSIX path, language, sha256, size_bytes, ignored state.
- `LogicalSymbol`: id, repo_id, language, qualified_name, kind; identity may change on rename, linked by matching evidence.
- `SymbolOccurrence`: id, snapshot_id, logical_symbol_id, source_file_id, spans, signature/content hash, parse_status.
- `Relation`: id, snapshot_id, from_occurrence or canonical node, to_occurrence or unresolved target, relation_type, status, confidence_class, resolver, evidence_ids.
- `Evidence`: id, source_file_id, snapshot_id, path, span, content_hash, extractor, extractor_version, notes.
- `IndexJob`: status, started_at, finished_at, counts, failures, manifest hashes.

### Identidade e estabilidade
- Diferenciar `repo ID` de pathname local: mover a pasta não deve criar outro repositório automaticamente.
- `LogicalSymbol` e `SymbolOccurrence` são entidades diferentes. Span e hash pertencem à ocorrência.
- Relacionamentos nunca cruzam snapshots de forma implícita: consultas multi-snapshot exigem versão explícita.
- IDs de conteúdo e relações devem ter canonização, versionamento e testes determinísticos.
- `confidence_class`: `resolved`, `syntactic`, `heuristic`, `inferred`; não confundir com probabilidade estatística.
- `resolution_status`: `resolved`, `ambiguous`, `unresolved`, `external`.

### Tipos iniciais
- Nodes: `repository`, `package`, `module`, `file`, `class`, `interface`, `function`, `method`, `symbol`, `config`, `unknown`.
- Edges: `CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `IMPLEMENTS`, `EXTENDS`, `REFERENCES`, `CONFIGURES`, `TESTS`.
- Futuro: `READS_FROM`, `WRITES_TO`, `DEPLOYS`, `DOCUMENTED_BY`, `EXPOSES`, `CONSUMES`.
- `AFFECTS` deve ser output derivado de consultas e não fato estrutural persistido sem justificativa.

## 6. Persistência e fluxo de indexação

### SQLite
- Tabelas propostas: `schema_migrations`, `repositories`, `snapshots`, `source_files`, `symbols`, `symbol_occurrences`, `relations`, `evidence`, `index_jobs`, `file_inventory`, `fts_symbols`.
- índices: `snapshot_id`, `(repository_id, relative_path)`, `(snapshot_id, source_file_id)`, `(from_id, type)`, `(to_id, type)`.
- WAL mode, busy timeout, único escritor por workspace (lock e retry controlados), readers concorrentes.
- FTS5 para nomes, qualificadores e eventualmente docstrings; não copiar segredos para o índice.
- Nunca construir SQL com concatenação de query do usuário.

### Pipeline
1. Validar root e limites/permissions.
2. Descobrir manifest Git-aware, respeitar ignore patterns; definir comportamento para worktree não commitado.
3. Comparar fingerprints e versões dos extratores com o último snapshot.
4. Criar novo snapshot em staging (`BUILDING`).
5. Parsear apenas arquivos adicionados, alterados ou invalidados; desfazer índices de removidos no novo snapshot.
6. Resolver símbolos, dependências de símbolos e referências afetadas.
7. Validar integridade (FKs, evidências, isolamento do snapshot), atualizar FTS.
8. Publicar atomicamente `READY`; se falhar, preservar snapshot anterior e diagnosticar.
9. Retention e GC explícitos, auditáveis.

### Comportamentos difíceis a testar
- Renomeação, exclusão, symlink, Git worktree, arquivo binário, arquivo imenso, UTF-8 inválido.
- Merge conflict markers, parser errors, changes durante scan, case-insensitive Windows paths.
- Processos simultâneos, Ctrl-C, crash após staging, update de parser, branch switch.
- No-op index não refaz parse nem altera o snapshot ativo desnecessariamente.

## 7. Interfaces públicas (propostas; ainda não implementadas)

### CLI
```
code-atlas --version
code-atlas doctor
code-atlas init [--root PATH]
code-atlas index [PATH] [--json] [--full]
code-atlas status [--json]
code-atlas search QUERY [--limit N] [--json]
code-atlas symbol SYMBOL_ID [--json]
code-atlas references SYMBOL_ID [--json]
code-atlas graph [--snapshot ID] [--json]
```

Futuro: `impact`, `context`, `mcp`, `serve`, `ui`, `workspace`, `plugin`. O executável não depende de Forge CLI.

### API v1 (Cycle 4+)
- `GET /healthz`, `GET /v1/status`, `GET /v1/repositories`, `GET /v1/snapshots`
- `POST /v1/search`, `GET /v1/symbols/{id}`, `GET /v1/symbols/{id}/references`
- `POST /v1/graph/neighborhood`, `POST /v1/impact`, `POST /v1/context-packs`
- Paginação baseada em cursor, `snapshot_id` requerido em consultas consistentes, erros tipados, request_id.

### MCP (Cycle 4+)
Tools propostas: `atlas_search`, `atlas_symbol`, `atlas_references`, `atlas_dependencies`, `atlas_impact`, `atlas_context`, `atlas_architecture`, `atlas_changes`, `atlas_explain`.
Server stdio inicialmente, HTTP com segurança em etapa posterior. Sem publicar uma tool que não tenha implementação e testes reais. Referência MCP 2026-07-28; negociar compatibilidade com clients.

## 8. Pesquisa de corpus e baseline

Comparações com CKB, CocoIndex Code, Aider e Sourcegraph/SCIP, sempre citando versão, hardware, linguagem, licença e condições. Baselines mínimas:
- grep/ripgrep + leitura manual;
- FTS puro;
- AST-only;
- AST + semantic index quando disponível;
- contexto de arquivos inteiros contra context packs com orçamento equivalente.

Fixtures sintéticos com ground truth + alguns repos reais, sem expor código proprietário em telemetria.
Métricas: symbol precision/recall por linguagem, edge precision/recall por tipo, unresolved rate, change invalidation correctness, reindex speedup, query p50/p95, memory peak, token usage, task accuracy, regressions e false positives.

## 9. Segurança / confiança

- Não rodar `pip install`, `npm install`, scripts, build, `terraform`, plugins não confiáveis ou código do repo durante a análise por padrão.
- Scripts e indexadores externos apenas por opt-in com isolação, timeout, limites de recursos e allowlist de comandos.
- Arquivos, docs, comentários e READMEs são dados não confiáveis e nunca prevalecem sobre instruções do agente.
- Segredos excluídos por patterns e redaction; não transmitir arquivo completo para provedores LLM sem autorização explícita.
- Endpoints de serviço não escutam interfaces públicas por padrão; autenticação e autorização para servidor remoto.
- Proteção contra path traversal, symlink escape, zip bomb, payloads enormes e DoS de consultas de grafo.
- SPDX/SBOM, dependabot/renovate, licença de dependências, scanners SAST/SCA, release OIDC quando aplicável.

## 10. UX Explorer (Cycle 6)

Visões: architecture map, dependency explorer, impact explorer, evolution timeline, execution/data flow (inference vs runtime clearly marked). Angular + Sigma.js + Graphology propostos; benchmark e fallback para Cytoscape.js conforme tipo de grafo. Filtro/expansão progressiva, agrupamento, viewport culling, layout determinístico, limites explícitos, minimap, keyboard navigation, tooltips evidenciados, links ao arquivo/commit, acessibilidade.

Test fixtures: 1k/3k/10k/50k nós (com densidades de aresta diferentes). Medir FPS, carregamento, heap, câmera, agrupamento e reação a cancelamento de layout; sem promessa antecipada de suporte a 50k.

## 11. Forge interoperability

- Descoberta por manifest de capability e contratos JSON Schema versionados; integração opt-in.
- The Forge orquestra; Atlas conhece código; Spark Forge interpreta domínio AWS/Azure; Forge Doctor audita; Forge Lens traça; Forge Portal visualiza; Forge CLI instala. Ninguém precisa depender dos outros para executar funções primárias.
- Identificadores de correlação: repository, commit, source file, symbol occurrence, tool call / trace IDs.
- Sem copiar as rules/knowledge de outras Forjas para o Atlas; compartilhar referências via adapters declarados.

## 12. Matriz de ciclos e gates

### Cycle 1 — Foundation & Contracts
Entregas: pacote instalável, CLI `doctor/init/status`, config, discovery Git-aware, modelos e schemas v1, SQL migrations, CI lint/types/tests/security, ADRs, exemplos, comandos smoke test. Gate: instalar e descobrir repositório isoladamente Windows/Linux; sem dependências externas obrigatórias.

### Cycle 2 — Structural Intelligence
Tree-sitter via adapters Python/TypeScript inicialmente, file/module/symbol graph, snapshots determinísticos, search structural básico. Gate: golden fixtures com AST e grafo corretos; erros de parsing rastreáveis.

### Cycle 3 — Semantic & Incremental
SCIP/LSP optional, invalidation dependency-aware, builds transacionais, snapshots prontos, rename/delete/branch. Gate: no-op reindex sem parse; consistência sob falhas e concorrência.

### Cycle 4 — Search & Agent Context
FTS+graph+vector opt-in, MCP stdio e context packs, budget, evidence. Gate: métricas e tokens versus baseline, nenhum símbolo fictício.

### Cycle 5 — Impact Engine
Blast radius, diff, tests, contracts, impact report, integração PR opcional. Gate: precision/recall auditável e distinção known/possible.

### Cycle 6 — Explorer
Angular e grafo; arquitetura progressiva, impact graph, zoom/filtros e UX. Gate: testes acessibilidade/E2E/performance, sem câmera que desaparece.

### Cycle 7 — Domain Adapters / Federation
SQL/PySpark/Iceberg/Glue, APIs, Terraform, CI, multi-repo, integrações Forge opcionais. Gate: fixtures cross-repo e domínio; independência entre pacotes.

### Cycle 8 — Production Quality
Hardening, benchmark competitivo, otimizador, observabilidade, compatibilidade, release, SBOM. Gate: releases reproduzíveis, roadmap/documentação e revisão externa de achados.

## 13. Definition of Done global

Para cada ciclo exigir: código funcional, testes escritos e executados, matriz de compatibilidade, resultados reproduzíveis, evidências de execução com logs redigidos, documentação atualizada, riscos residuais, failures known e backlog priorizado. Nada de declarar sucesso quando o teste foi pulado ou indisponível. Sem commits ou PRs falsamente alegados. Não marcar `DONE` se persistirem falhas P0/P1.

## 14. Referências

- Tree-sitter: https://tree-sitter.github.io/tree-sitter/
- SCIP: https://github.com/scip-code/scip
- SQLite FTS5: https://www.sqlite.org/fts5.html
- MCP 2026-07-28: https://blog.modelcontextprotocol.io/posts/2026-07-28/
- Code Property Graph: https://cpg.joern.io/
- Sigma: https://www.sigmajs.org/
- CKB architecture: https://codeknowledge.dev/docs/Architecture
- CocoIndex: https://cocoindex.io/docs/
- Aider repository map: https://aider.chat/docs/repomap.html
