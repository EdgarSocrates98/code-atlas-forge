# Code Atlas Forge

Plataforma **open source, local-first e offline-first** de inteligência estrutural,
semântica e arquitetural de software — grafos tipados, proveniência verificável,
indexação incremental e contexto econômico para agentes.

> **Status atual: Cycle 2 — Structural Intelligence.**
> O Code Atlas indexa repositórios com parsing real (Tree-sitter), extrai
> símbolos e constrói um grafo tipado persistido em SQLite, consultável via
> `search`/`symbol`/`references`/`graph`. **Não há** resolução semântica
> profunda (SCIP/LSP), impacto, MCP ou UI ainda — veja `docs/roadmap.md`.

## Princípios

- **Local-first:** nenhum serviço externo, Docker, conta SaaS ou API key é
  necessário para operar. Nenhum dado sai da máquina por padrão.
- **Sem telemetria:** não existe código de telemetria no projeto — a coleta
  não é apenas desligada por padrão, ela está ausente.
- **O repositório indexado é não confiável:** nada do código varrido é
  executado, importado ou instalado.
- **Evidência primeiro:** toda relação carrega origem, extrator, versão,
  confiança e status de resolução.
- **Determinismo:** a mesma árvore produz o mesmo grafo e os mesmos ids.

A especificação arquitetural normativa está em `docs/BLUEPRINT-v1.md`.

## Instalação

Requer Python 3.12+. Dependências de runtime pinned: `tree-sitter` +
gramáticas (ADR-015) — instaladas automaticamente via wheels.

```bash
git clone https://github.com/EdgarSocrates98/code-atlas-forge.git
cd code-atlas-forge
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
code-atlas --version
code-atlas doctor
```

Para desenvolvimento: `pip install -e .[dev]`.
Detalhes em `docs/install.md`.

## Uso rápido

```bash
code-atlas init --root /caminho/do/repo   # configura o workspace (XDG/APPDATA)
code-atlas doctor                        # verifica ambiente e config
code-atlas index /caminho/do/repo        # scan → parse → grafo → snapshot READY
code-atlas status --json                 # estado do índice, git, migrações

# consultas estruturais sobre o snapshot READY
code-atlas search "Engine" --json        # substring em qualified_name
code-atlas symbol pkg.core.Engine        # detalhe + ocorrências
code-atlas references pkg.util.helper    # relações tocando o símbolo
code-atlas graph --json                  # dump de nós+arestas do snapshot
```

Filtros: `--language`, `--kind`, `--path`, `--snapshot`,
`--relation-type`, `--limit`/`--offset`. Saída JSON estável com
`schema_version`. Detalhes: `docs/structural-search.md`.

Configuração: `--root` > variáveis `CODE_ATLAS_*` > `XDG_CONFIG_HOME` /
`%APPDATA%`. Nada é escrito dentro do repositório indexado.

## Linguagens suportadas (Cycle 2)

Python (`.py`/`.pyi`), TypeScript (`.ts` variantes), TSX (`.tsx`) —
matriz completa com versões de gramática pinned em
`docs/parser-adapters.md`. Arquivos de outras linguagens entram no
inventário como nós `file`, sem extração.

## Limitações do Cycle 2

Implementado hoje: parsing sintático real, símbolos/referências/
relações com proveniência, resolução de escopo sintático (imports +
definidos locais), snapshots atômicos, busca estrutural SQL.

**Não implementado** (por design): resolução semântica profunda,
inferência de tipos, análise de impacto, FTS na busca, context packs,
MCP, UI. Estados `unresolved`/`ambiguous`/`external` são preservados
como fatos — nunca preenchidos por heurística.

## Estrutura do repositório

```
src/code_atlas/     pacote (layout src; deps pinned = tree-sitter)
  contracts/        modelos de domínio, enums, ports (hexagonal)
  discovery/        scanner Git-aware + manifest de inventário
  parsers/          engine + registry + adaptadores tree-sitter
  graph/            builder canônico, identidades, validação
  storage/          SQLite: migrações v1-v3, snapshots, FTS5
  indexing/         coordinator + persistência do grafo
  search/           StructuralSearch (SQL parametrizado)
  observability/    logging estruturado + redaction (sem telemetria)
tests/              suíte pytest (unitários, golden corpus, e2e, bench)
docs/               blueprint, ADRs, roadmap, schemas, evidências
factory/            specs Loop Factory (inbox/active/archive + audit)
benchmarks/         bench_scan (cycle-1) + bench_index (cycle-2)
```

## Desenvolvimento

```bash
pip install -e .[dev]
ruff check src tests && mypy src && pytest -q
```

## Licença

Apache-2.0 — veja `LICENSE` e `NOTICE`.
