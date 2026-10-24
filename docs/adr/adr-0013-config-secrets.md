# ADR-013: Configuração XDG/AppData e tratamento de segredos

**Status:** decidida e implementada (Cycle 1)

## Contexto

O usuário precisa de config portável por plataforma e o sistema nunca pode
vazar segredos do repo indexado para estado persistido ou logs.

## Decisão

Resolução `--root` > `CODE_ATLAS_*` > XDG (`~/.config/code-atlas`,
`~/.local/share/code-atlas`) / APPDATA/LOCALAPPDATA no Windows. `config.toml`
tem `schema_version` e nunca é sobrescrito sem `--force`. Padrões de segredo
(`.env*`, `*.pem`, `*.key`, `id_*`, `credentials*`) são excluídos de hash e
inventário — só `secret_count`. `RedactionFilter` mascara `$HOME` e
atribuições sensíveis em todos os logs.

## Consequências

- `CODE_ATLAS_CONFIG_DIR`/`CODE_ATLAS_DATA_DIR` isolam testes sem tocar o
  home real (fixtures usam isso).
- Nada é escrito no repo indexado — invariante testada.
