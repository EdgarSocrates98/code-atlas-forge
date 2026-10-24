# Code Atlas Forge

Plataforma **open source, local-first e offline-first** de inteligência estrutural,
semântica e arquitetural de software — grafos tipados, proveniência verificável,
indexação incremental e contexto econômico para agentes.

> **Status atual: Cycle 1 — Foundation & Contracts.**
> Hoje o Code Atlas instala, configura o workspace, descobre o inventário de
> arquivos com fingerprints e persiste snapshots em SQLite. **Não há** parsing
> AST, resolução semântica (SCIP/LSP), busca, impacto, MCP ou UI ainda — veja
> `docs/roadmap.md` e a seção [Limitações](#limitações-do-cycle-1).

## Princípios

- **Local-first:** nenhum serviço externo, Docker, conta SaaS ou API key é
  necessário para operar. Nenhum dado sai da máquina por padrão.
- **O repositório indexado é não confiável:** nada do código varrido é
  executado, importado ou instalado.
- **Evidência primeiro:** toda relação carrega origem, extrator, versão,
  confiança e status de resolução.
- **Determinismo:** o mesmo inventário produz o mesmo manifest.

A especificação arquitetural normativa está em `docs/BLUEPRINT-v1.md`.

## Instalação

Requer Python 3.12+.

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
Detalhes em `docs/install.md` (Cycle 1, spec lf-0006).

## Uso rápido

```bash
code-atlas init --root /caminho/do/repo   # configura o workspace (XDG/APPDATA)
code-atlas doctor                        # verifica ambiente e config
code-atlas index /caminho/do/repo        # inventário + fingerprints → snapshot
code-atlas status --json                 # estado do índice, git, migrações
```

Configuração: `--root` > variáveis `CODE_ATLAS_*` > `XDG_CONFIG_HOME` /
`%APPDATA%`. Nada é escrito dentro do repositório indexado.

## Limitações do Cycle 1

Implementado hoje: packaging, configuração, discovery Git-aware, inventário com
fingerprints SHA-256, contratos de domínio versionados e snapshots SQLite com
publicação atômica.

**Não implementado** (por design, próximos ciclos): AST/Tree-sitter, símbolos,
arestas de grafo reais, SCIP/LSP, busca FTS, análise de impacto, context packs,
servidor MCP, API REST e explorer Angular.

## Estrutura do repositório

```
src/code_atlas/     pacote (layout src, zero dependências de runtime)
  contracts/        modelos de domínio, enums, ports (hexagonal)
  discovery/        scanner Git-aware + manifest de inventário
  storage/          SQLite: migrações, snapshots, FTS
  indexing/         coordenador de indexação / publicação atômica
  observability/    logging estruturado + redaction (sem telemetria)
tests/              suíte pytest (fixtures sintéticos)
docs/               blueprint, ADRs, roadmap, threat model, evidências
factory/            specs Loop Factory (inbox/active/archive + audit trail)
benchmarks/         corpora sintéticos e medições de scan
```

## Desenvolvimento

```bash
pip install -e .[dev]
ruff check src tests && mypy src && pytest -q
```

## Licença

Apache-2.0 — veja `LICENSE` e `NOTICE`.
