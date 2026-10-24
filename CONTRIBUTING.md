# Contribuindo com o Code Atlas Forge

Obrigado pelo interesse! Este documento descreve o fluxo mínimo de contribuição.

## Setup

```bash
git clone https://github.com/EdgarSocrates98/code-atlas-forge.git
cd code-atlas-forge
python3 -m venv .venv && source .venv/bin/activate
pip install -e .[dev]
```

## Gates obrigatórios antes de abrir PR

```bash
ruff check src tests
mypy src
pytest -q
python -m build
```

Todos devem passar. Se algo não puder rodar no seu ambiente, descreva no PR como
`NOT RUN` — nunca declare sucesso que não foi executado.

## Regras do projeto

- **Zero dependências de runtime** além da stdlib (Cycle 1). Novas dependências
  exigem justificativa e revisão explícita.
- O domínio (`contracts`, `discovery`, `indexing`, `storage`) não importa CLI,
  web, MCP, UI nem outras Forjas — dependências fluem para contratos.
- Nenhum código do repositório indexado pode ser executado durante análise.
- Testes acompanham toda mudança de comportamento.
- Specs de trabalho vivem em `factory/specs/` (Loop Factory); a fonte de verdade
  do que construir é a spec, não o histórico de chat.

## Estilo

- `ruff` (lint + formatação), `mypy --strict`, docstrings em APIs públicas.
- Identificadores e contratos em inglês; documentação de usuário em pt-BR.
- Commits pequenos e revisáveis; mensagens descrevem o porquê.

## Reportando bugs

Use o template de issue em `.github/ISSUE_TEMPLATE/`. Inclua versão do Python,
SO, comando executado, saída real e o que era esperado.
