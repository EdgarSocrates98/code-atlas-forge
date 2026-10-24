# Quickstart — Cycle 1

Fluxo mínimo: init → doctor → index → status. Todos os comandos abaixo
foram executados verbatim nesta máquina (evidência completa em
`docs/cycle-1-evidence.md`).

## 1. Instalar

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install .
```

## 2. Verificar ambiente

```bash
$ code-atlas doctor
python    ok    3.12.3
sqlite    ok    3.45.1
fts5      ok    available
git       ok    git version 2.43.0
config    ok    /home/edgar/.config/code-atlas/config.toml  missing (run `code-atlas init`)
data_dir  ok    /home/edgar/.local/share/code-atlas  writable via /home/edgar/.local/share
```

## 3. Inicializar workspace

```bash
$ code-atlas init --root /caminho/do/repo
config:  /home/edgar/.config/code-atlas/config.toml
root:    /caminho/do/repo
data:    /home/edgar/.local/share/code-atlas
```

## 4. Indexar

```bash
$ code-atlas index /caminho/do/repo --json
{"tool": "code-atlas", "version": "0.1.0", "root": "...",
 "snapshot_id": 1, "status": "success", "files_indexed": 116,
 "secrets_skipped": 3, "warnings": [], "noop": false}
```

Re-indexar sem mudanças é **no-op determinístico**:
`{"status": "noop", "files_indexed": 0, ...}`.

## 5. Estado

```bash
$ code-atlas status
root:       /caminho/do/repo
schema:     v2
git:        main@8d1551b91e41 (dirty)
snapshot:   #1 READY — 116 files
```

## O que o Cycle 1 **não** faz

`search`, `symbol`, `references`, `graph`, `impact`, `context`, `mcp`,
`serve`, `ui` — não existem e não aparecem no `--help`. O inventário é de
arquivos+fingerprints; ainda não há símbolos nem grafo (Cycles 2–3).

## Troubleshooting

`init` falha com "config already exists" → use `--force` ou aponte
`CODE_ATLAS_CONFIG_DIR` para outro lugar. Mais em `docs/troubleshooting.md`.
