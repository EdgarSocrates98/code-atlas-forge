# Instalação — Code Atlas Forge

Requisito: **Python 3.12+**. Sem dependências de runtime (stdlib apenas),
sem Docker, sem conta externa.

## Linux

```bash
git clone https://github.com/EdgarSocrates98/code-atlas-forge.git
cd code-atlas-forge
python3 -m venv .venv
source .venv/bin/activate
pip install .
code-atlas --version   # code-atlas 0.1.0
code-atlas doctor      # probes reais: python, sqlite, fts5, git, config
```

## Windows

```powershell
git clone https://github.com/EdgarSocrates98/code-atlas-forge.git
cd code-atlas-forge
python -m venv .venv
.venv\Scripts\activate
pip install .
code-atlas --version
code-atlas doctor
```

> A leg Windows é exercida em CI (`.github/workflows/ci.yml`,
> `windows-latest` × Python 3.12/3.13) — as instruções acima refletem os
> mesmos passos executados na matriz.

## pipx (opcional)

```bash
pipx install /caminho/para/code-atlas-forge
```

## Verificação de venv limpo

```bash
python3 -m venv /tmp/atlas-clean
/tmp/atlas-clean/bin/pip install /caminho/code-atlas-forge
/tmp/atlas-clean/bin/code-atlas --version
/tmp/atlas-clean/bin/code-atlas doctor
```

## Desenvolvimento

```bash
pip install -e .[dev]        # pytest, ruff, mypy, build
ruff check src tests && mypy src && pytest -q
python -m build              # gera dist/*.whl
```

## Desinstalação

```bash
pip uninstall code-atlas-forge
rm -rf ~/.config/code-atlas ~/.local/share/code-atlas   # config + banco
```
