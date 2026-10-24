# CODE ATLAS FORGE — MASTER IMPLEMENTATION PROMPT
## Cycle 2: Structural Intelligence & Code Graph Foundation
### Inclui fechamento dos gaps do Cycle 1

**Repositório oficial:** https://github.com/EdgarSocrates98/code-atlas-forge  
**Branch de referência:** main  
**Blueprint normativo:** docs/BLUEPRINT-v1.md  
**Roadmap:** docs/roadmap.md  
**Idioma da documentação:** pt-BR  
**Idioma de interfaces, contratos e código:** inglês

---

# 0. Missão e comportamento do agente

Você é o responsável técnico pela implementação integral do Cycle 2 do Code Atlas Forge.

Atue como um time multidisciplinar de engenharia, envolvendo:

- Software Architect
- Python Engineer
- Compiler/AST Engineer
- Static Analysis Engineer
- Graph/Data Modeling Engineer
- SQLite Engineer
- Security Engineer
- QA/Test Automation Engineer
- Developer Experience Engineer
- Agentic Engineering Specialist

Utilize as skills, agents, subagents, ferramentas MCP e recursos locais disponíveis que sejam relevantes para cada especialidade.

Não suponha que as skills estejam instaladas: descubra, avalie e utilize somente as disponíveis e apropriadas.

Adote uma implementação evidence-driven, orientada a testes, contratos, determinismo e compatibilidade.

**O Cycle 2 não pode terminar com mocks que apenas simulam análise de código. Deve existir parsing real, extração real de símbolos e construção real do grafo.**

## Regras inegociáveis

1. Nunca apagar funcionalidades entregues no Cycle 1 para simplificar a implementação.
2. Não alterar contratos existentes sem justificar, versionar e testar a migração.
3. Não gerar símbolos ou dependências fictícios.
4. Não usar LLM como fonte primária da verdade estrutural.
5. Não executar o código dos repositórios indexados.
6. Preservar a arquitetura local-first e offline-first.
7. Não criar dependência obrigatória de nenhuma Forge externa.
8. Não exigir Docker, serviços externos ou API keys.
9. Não afirmar que testes passaram sem executá-los.
10. Não modificar a main diretamente: usar uma branch de implementação, abrir PR e concluir merge somente após os gates necessários.

---

# PHASE 0 — Auditoria e fechamento dos gaps do Cycle 1

Antes de implementar as novas funcionalidades:

- Ler integralmente o Blueprint, roadmap, ADRs, evidence report e contratos.
- Identificar o SHA atual da main.
- Inspecionar árvore de arquivos, dependências, migrations e testes.
- Executar lint, mypy, pytest, build e smoke tests.
- Consultar GitHub Actions e registrar o estado real da matriz.
- Elaborar uma matriz PASS / FAIL / NOT RUN com evidências.

## P1.1 — Symlink escape

Auditar a varredura Git-aware.

Investigar especialmente:

- `_git_paths()`
- `_classify_and_hash()`
- `_hash_file()`
- `scan()`

Garantir que symlinks não permitam leitura fora da raiz autorizada, inclusive quando rastreados pelo Git.

Implementar testes com symlinks de arquivos e diretórios, destinos externos, symlinks quebrados e alterações de destino durante leitura.

Adotar medidas proporcionais contra condições TOCTOU, incluindo validação do descritor efetivamente aberto quando suportada pela plataforma.

## P1.2 — CI Windows

Corrigir seleção e instalação de wheels sem depender de expansão de glob específica do Bash.

Homologar:

- Ubuntu + Python 3.12
- Ubuntu + Python 3.13
- Windows + Python 3.12
- Windows + Python 3.13

Validar lint, typecheck, pytest, build, instalação limpa e execução real da CLI.

## P1.3 — Migrações transacionais

Auditar o uso de `sqlite3.executescript()`.

Garantir que falhas durante migrations não deixem estados parcialmente aplicados ou registros falsos de sucesso.

Testar rollback, checksum drift, reexecução idempotente, falhas de DDL e abertura de bancos antigos.

Não editar migrations já publicadas de forma incompatível. Criar novas migrations versionadas quando necessário.

## P2 — Hardening adicional

Revisar identidade de repositório, mudanças de branch, caminhos com diferenças de caixa, arquivos ilegíveis, concorrência, cancelamento e recuperação após falhas.

Registrar o que foi corrigido e o que permanece como dívida técnica.

**Gate 0:** nenhuma regressão do Cycle 1; P1 sanados ou bloqueios explicitamente documentados.

---

# PHASE 1 — Architecture & Parser Contracts

Criar um subsistema real de parsing por adaptadores.

Estrutura sugerida:

```text
src/code_atlas/
  parsers/
    __init__.py
    contracts.py
    registry.py
    engine.py
    tree_sitter_runtime.py
    languages/
      python.py
      typescript.py
      tsx.py
  graph/
    __init__.py
    identity.py
    builder.py
    validation.py
    queries.py
  search/
    __init__.py
    structural.py
```

Definir contratos tipados:

- `ParserAdapter`
- `ParserCapability`
- `ParseRequest`
- `ParseResult`
- `ExtractedSymbol`
- `ExtractedReference`
- `ExtractedRelation`
- `ParseDiagnostic`
- `SourceSpan`
- `GraphBuildResult`

Cada adaptador deve informar sua linguagem, extensões, versão do parser, gramática, capacidades e limitações.

Separar claramente parsing sintático de resolução semântica.

O código de domínio não pode importar implementações concretas de Tree-sitter ou SQLite.

**Gate 1:** contratos versionados, documentação e testes de arquitetura/dependência.

---

# PHASE 2 — Tree-sitter Runtime

Integrar Tree-sitter com versões controladas e compatíveis das gramáticas Python, TypeScript e TSX.

A implementação precisa:

- Carregar parsers por linguagem.
- Aceitar arquivos e buffers.
- Preservar posições de linha e coluna.
- Lidar corretamente com Unicode, UTF-8 e offsets em bytes.
- Reconhecer árvores parcialmente válidas.
- Registrar nós `ERROR` e situações `MISSING`.
- Aplicar limites de arquivo, tempo e memória.
- Produzir diagnósticos estruturados.
- Não executar nem importar o código analisado.

Arquivos inválidos ou incompletos não podem provocar crash do processo inteiro.

Uma falha de parsing em arquivo suportado deve ser registrada de forma explícita, sem transformar automaticamente uma análise incompleta em um snapshot íntegro.

Definir política para erros recuperáveis versus erros fatais.

**Gate 2:** parsing real e reproduzível de fixtures Python, TypeScript e TSX.

---

# PHASE 3 — Symbol Extraction

Implementar a extração estrutural de símbolos.

## Python

Reconhecer, no mínimo:

- Módulos
- Classes
- Funções
- Métodos
- Funções assíncronas
- Métodos assíncronos
- Funções aninhadas
- Imports e aliases
- Decorators
- Herança declarada
- Parâmetros
- Assinaturas
- Docstrings
- Escopo léxico

## TypeScript/TSX

Reconhecer, no mínimo:

- Modules
- Classes
- Interfaces
- Functions
- Methods
- Arrow functions quando nomeáveis
- Imports/exports
- Aliases
- Constructors
- Inheritance
- Interface implementations declaradas
- Type declarations
- Async functions
- JSX/TSX constructs relevantes

Não criar uma identidade canônica de função para toda expressão anônima sem uma política documentada.

## Identidade

Implementar separação entre:

- Símbolo lógico
- Ocorrência no snapshot
- Localização no arquivo
- Hash de conteúdo
- Nome qualificado
- Linguagem
- Tipo de símbolo

Garantir que símbolos diferentes em escopos diferentes não colidam.

Não utilizar número de linha como identidade permanente.

**Gate 3:** fixtures com símbolos aninhados, aliases, overloads, decorators e assinaturas complexas aprovadas.

---

# PHASE 4 — Canonical Graph Builder

Implementar a primeira construção real de grafos de código.

Tipos prioritários:

- REPOSITORY
- PACKAGE
- MODULE
- FILE
- CLASS
- INTERFACE
- FUNCTION
- METHOD
- SYMBOL

Relações prioritárias:

- CONTAINS
- DEFINES
- IMPORTS
- CALLS
- EXTENDS
- IMPLEMENTS
- REFERENCES

Respeitar rigorosamente o Blueprint e os enums existentes.

Não representar chamadas dinâmicas como resolvidas apenas por coincidência textual.

Exigir proveniência para as relações persistidas:

- Arquivo
- Snapshot
- Span
- Extrator
- Versão do extrator
- Confidence class
- Resolution status

Quando o alvo de uma chamada não for resolvido com segurança, preservar o alvo textual e marcar `unresolved` ou `ambiguous`.

A resolução semântica profunda via SCIP/LSP pertence ao Cycle 3, mas o Cycle 2 pode resolver relações locais inequívocas com base em escopo sintático.

Validar integridade referencial, ausência de relações cruzadas entre snapshots e consistência de IDs.

**Gate 4:** grafo real, tipado e determinístico para fixtures com ground truth.

---

# PHASE 5 — SQLite Storage & Snapshot Integration

Integrar parsers e graph builder ao IndexCoordinator existente.

Criar migrations incrementais necessárias para persistir ocorrências, relações, evidências e metadados de análise.

Fluxo esperado:

```text
Discovery
  -> Manifest
  -> Parser Registry
  -> Extracted Symbols
  -> Graph Builder
  -> Integrity Validation
  -> Snapshot READY
```

Preservar o snapshot READY anterior em caso de erro de persistência ou indexação não recuperável.

Implementar inserções em lotes quando apropriado, sem perder atomicidade.

Incorporar versões das gramáticas e extratores ao cálculo de identidade do índice.

Um inventário inalterado, mas com versão de extrator alterada, não pode resultar em no-op incorreto.

Para arquivos estruturalmente idênticos, garantir que a saída de parsing e do grafo seja determinística.

Não antecipar de maneira artificial todas as otimizações do Cycle 3. Preparar o contrato incremental, mas manter clara a diferença entre indexação estrutural funcional e invalidação semântica completa.

**Gate 5:** indexação persistente, snapshots íntegros, rollback validado e ausência de duplicações indevidas.

---

# PHASE 6 — Structural Search & CLI

Disponibilizar consultas reais sobre os dados persistidos.

Implementar conforme os contratos do Blueprint:

```bash
code-atlas search "process_orders"
code-atlas search "process_orders" --json

code-atlas symbol <symbol-id> --json

code-atlas references <symbol-id> --json

code-atlas graph --json
code-atlas graph --snapshot <id> --json
```

Adicionar filtros coerentes com o modelo:

- Linguagem
- Tipo de símbolo
- Caminho
- Repositório
- Snapshot
- Nome qualificado
- Tipo de relação

Adicionar paginação ou limites de resultados para impedir saídas gigantescas.

A busca estrutural deve funcionar offline e sem embeddings.

Saídas JSON devem ser estáveis, versionadas e documentadas.

Garantir códigos de saída consistentes e erros úteis.

O comando `graph` deve poder produzir representações JSON de nós e arestas, sem depender de uma UI.

Não implementar ainda o Explorer Angular completo.

**Gate 6:** todas as consultas retornam dados reais extraídos e persistidos.

---

# PHASE 7 — Golden Fixtures & Evaluation

Criar um conjunto de avaliação com resultado esperado conhecido.

Cobrir:

- Python simples e avançado
- TypeScript
- TSX
- Múltiplos arquivos
- Imports locais e externos
- Aliases
- Funções aninhadas
- Herança
- Interfaces
- Decorators
- Chamadas não resolvidas
- Código sintaticamente inválido
- Unicode
- Arquivos vazios
- Arquivos grandes
- Renomeação, exclusão e atualização
- Símbolos com nomes repetidos
- Projetos sem Git

Adicionar testes:

1. Unitários dos adaptadores.
2. Integração entre parsing e grafo.
3. Persistência SQLite.
4. Consistência de snapshots.
5. CLI E2E.
6. Segurança e limites.
7. Determinismo.
8. Regressões do Cycle 1.

Medir precision e recall separadamente para símbolos e relações onde houver ground truth.

Não misturar relações sintáticas e semânticas resolvidas na mesma métrica sem distinção.

Guardar snapshots de saída esperada para detectar mudanças acidentais de gramáticas.

**Gate 7:** testes executados, resultados reproduzíveis e limitações explicitadas.

---

# PHASE 8 — Benchmark & Performance

Construir corpora controlados com diferentes tamanhos e densidades de símbolos.

Ensaios sugeridos:

- 100 arquivos
- 1.000 arquivos
- 5.000 arquivos
- 10.000 arquivos

Medir:

- Tempo de discovery
- Tempo de parsing
- Tempo de construção do grafo
- Tempo de persistência
- Tempo total de indexação
- Pico de memória
- Quantidade de símbolos
- Quantidade de arestas
- Consultas p50/p95
- Reindexação sem alterações
- Tamanho do banco SQLite

Comparar com a baseline do Cycle 1, deixando claro que ela mediu principalmente discovery e hashing, não análise AST.

Não inventar benchmarks nem afirmar ganhos não medidos.

Publicar hardware, sistema operacional, versão do Python, tamanho do corpus e metodologia.

**Gate 8:** relatório reproduzível com gargalos e limites conhecidos.

---

# PHASE 9 — Documentation & Developer Experience

Atualizar:

- README.md
- docs/architecture.md
- docs/database-schema.md
- docs/roadmap.md
- docs/quickstart.md
- docs/extensibility.md
- docs/troubleshooting.md
- ADRs afetados
- Matriz de linguagens suportadas

Criar:

- docs/cycle-2-evidence.md
- docs/cycle-2-architecture.md
- docs/parser-adapters.md
- docs/graph-schema.md
- docs/structural-search.md
- docs/cycle-2-benchmarks.md

Demonstrar um fluxo real:

```bash
code-atlas init --root .
code-atlas index . --json
code-atlas status --json
code-atlas search "main" --json
code-atlas graph --json
```

A documentação deve explicar a diferença entre:

- Parsing sintático
- Referência sintática
- Referência resolvida
- Inferência
- Análise semântica futura

Não usar resultados fictícios como se tivessem sido executados.

---

# PHASE 10 — Final Homologation

Antes de considerar o Cycle 2 concluído:

1. Executar toda a suíte local.
2. Executar Ruff e mypy strict.
3. Validar instalação limpa por wheel.
4. Validar CLI E2E.
5. Validar banco e migrations.
6. Validar segurança e ausência de egress.
7. Conferir determinismo de grafos.
8. Validar CI Linux/Windows.
9. Conferir ausência de dependências obrigatórias das Forjas.
10. Auditar a compatibilidade do Blueprint.
11. Verificar que os 115 testes anteriormente documentados não foram perdidos sem justificativa.
12. Realizar revisão adversarial dos novos parsers.
13. Gerar relatório final com PASS / FAIL / NOT RUN.
14. Abrir PR com evidências, e realizar merge somente após aprovação dos gates.

## Critérios finais de aceite

O Cycle 2 somente estará concluído quando:

- Tree-sitter estiver integrado de verdade.
- Python, TypeScript e TSX forem analisados.
- Símbolos reais forem extraídos.
- Grafos reais forem produzidos.
- Relações possuírem proveniência.
- Estados unresolved e ambiguous forem preservados.
- Snapshots forem consistentes.
- Busca estrutural funcionar.
- CLI expuser os resultados em JSON.
- Testes e benchmarks estiverem documentados.
- Os P1 identificados do Cycle 1 estiverem resolvidos.
- O CI estiver validado ou qualquer limitação for claramente declarada.

## Entrega final esperada

Produza um relatório contendo:

- SHA inicial e final.
- PR e commits relevantes.
- Arquitetura implementada.
- Arquivos criados ou alterados.
- Contratos adicionados.
- Migrations realizadas.
- Número de testes aprovados e falhos.
- Cobertura por subsistema.
- Métricas de parsing e grafo.
- Benchmarks executados.
- Evidências dos gates.
- Débitos P0/P1/P2.
- Limitações conhecidas.
- Estado de prontidão para o Cycle 3.

Não marque como concluído um requisito que não tenha evidência verificável.

**Resultado esperado:** Code Atlas Forge deixa de ser um inventariador de repositórios e passa a ser um mecanismo funcional, determinístico e auditável de inteligência estrutural sobre código.

---

## 5. O que espero encontrar na próxima auditoria

Após a implementação, o principal teste não será apenas verificar a existência dos diretórios `parsers/` e `graph/`.

Quero conseguir indexar um pequeno projeto Python e obter um resultado real como este:

```text
Repository: sample-project
Snapshot: READY

Files:       8
Modules:     6
Classes:     4
Functions:  21
Relations:  37

Languages:
  Python: 8 files

Graph:
  CONTAINS:   14
  DEFINES:    21
  IMPORTS:    2
```

<caption>Valores exclusivamente ilustrativos; não são resultados do repositório atual.</caption>

Depois deveremos conseguir selecionar uma função, visualizar sua ocorrência, identificar seu módulo, encontrar referências e rastrear evidências no arquivo de origem.

**Esse é o marco que valida a essência do Code Atlas.**

Minha recomendação é executar o prompt em uma branch dedicada ao Cycle 2 e manter o Cycle 3 reservado para resolução semântica com SCIP/LSP, atualização incremental dependency-aware e tratamento robusto de alterações entre snapshots. Isso evita antecipar funcionalidades que ainda dependem de uma base estrutural confiável.

A `main` foi avaliada em leitura; não modifiquei o repositório nem executei o prompt de implementação.