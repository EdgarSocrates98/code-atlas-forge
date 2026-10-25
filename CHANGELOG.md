# Changelog

Todas as mudanças notáveis deste projeto serão documentadas aqui.
Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/),
versionamento [SemVer](https://semver.org/lang/pt-BR/).

## [Unreleased]

### Adicionado — Cycle 7 (branch `cycle-7-domain-intelligence-federation`)

- **Adapters de domínio opt-in** (ADR-0010, schema v9): `sql`,
  `pyspark`, `aws-data`, `api`, `terraform`, `cicd` — extração estática
  stdlib-only, zero dependências novas, nada executado/conectado.
- **Grafo de domínio persistido**: `domain_entities`,
  `domain_entity_occurrences`, `domain_relations` com identidade
  posicional `dom:…` e estados honestos
  (`resolved`/`ambiguous`/`unresolved`/`external`).
- **Resolução cross-domain** (`--cross-domain`): updates + arestas por
  concordância de identidade qualificada, evidência dos dois lados ou
  nada, `ambiguous` nunca promovido.
- **Federação multi-repo** (schema v10): workspaces, pins de snapshot
  READY/STALE por membro, `view_id` determinístico, joins por
  `shared_key`, arestas federadas computadas (não persistidas), HTTP
  read-only — mutação apenas via `code-atlas federation`.
- **Busca**: faceta `domain` no SearchEngine + `FederatedSearchEngine`
  (`GET /search?workspace=`, `/federation/workspaces/{id}/search`).
- **Impacto**: blast radius misto símbolo+domínio no `ImpactEngine`
  com hops incertos rebaixando para `possible`.
- **Context packs de domínio**: seção `domain` aditiva (schema v1) no
  envelope v2 via `DomainContextEngine`.
- **API**: rotas `/domains*` (entities, neighbors, relations, lineage)
  e `/federation/workspaces*` — todas GET read-only.
- **CLI**: `index --domains/--cross-domain`, `domains
  list|entities|entity|lineage`, `federation create|add|pin|view`.
- **MCP**: `atlas_domains`, `atlas_domain_entity`,
  `atlas_domain_lineage`, `atlas_federation_view` (15 tools total).
- **Explorer**: rotas `/lineage`, `/contracts`, `/infra`, `/pipelines`,
  `/federation`; FA2 em Web Worker (RAF gap máx. 83 ms, era ~2300 ms).
- **Avaliação por goldens** (`evaluation/run_eval.py`): 7 cenários,
  inventário exaustivo de arestas + entidades proibidas.
- **Fixes**: crash de repaint de arestas no Sigma e tipo de
  `occurrence_id` no drill-down (encontrados pelo benchmark denso).

### Adicionado

- Scaffold do pacote `code-atlas-forge` (src layout, Python >=3.12, zero deps de runtime).
- CLI `code-atlas` com `--version`, `doctor` e `init` (spec lf-0001).
- Configuração por precedência `--root` > `CODE_ATLAS_*` > XDG/APPDATA.
- Licença Apache-2.0, NOTICE, README, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY.
- Specs Loop Factory do Cycle 1 em `factory/specs/`.
