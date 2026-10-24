# Configuração

## Ordem de precedência (ADR-013)

1. `--root PATH` no comando (`init`, `index`, `status`);
2. `CODE_ATLAS_ROOT` no ambiente;
3. diretório de trabalho atual (cwd).

## Variáveis de ambiente

| Variável | Efeito |
|---|---|
| `CODE_ATLAS_ROOT` | Raiz default do workspace (vencida por `--root`). |
| `CODE_ATLAS_CONFIG_DIR` | Sobrescreve o diretório do `config.toml`. |
| `CODE_ATLAS_DATA_DIR` | Sobrescreve o diretório de dados (banco, futuros artefatos). |
| `CODE_ATLAS_LOG_FORMAT` | `json` → logs JSON-lines em stderr. |
| `CODE_ATLAS_LOG_LEVEL` | `DEBUG`/`INFO`/`WARNING`/`ERROR` (default `WARNING`). |

## Diretórios por plataforma

| | Linux | Windows |
|---|---|---|
| config | `$XDG_CONFIG_HOME/code-atlas` → `~/.config/code-atlas` | `%APPDATA%\code-atlas` → `~/AppData/Roaming/code-atlas` |
| data | `$XDG_DATA_HOME/code-atlas` → `~/.local/share/code-atlas` | `%LOCALAPPDATA%\code-atlas` → `~/AppData/Local/code-atlas` |

## Arquivos

- `config.toml` — `schema_version`, `[workspace].root`, e a tabela
  opt-in `[resolvers]` (lf-0027): `[resolvers.scip].index_path` para
  índice SCIP externo e `[resolvers.lsp.servers.<nome>]` para servers
  LSP (`command`, `languages`, `enabled`). Ausente = nenhum resolver
  externo. Ver `docs/semantic-resolution.md`. Nunca sobrescrito
  sem `--force`.
- `<data_dir>/atlas.db` — SQLite WAL com migrações versionadas
  (`schema_version` queryável via `status`).

## Regras

- **Nada é escrito dentro do repositório indexado** — inventário e banco
  vivem no data dir. Teste dedicado garante (`test_nothing_written_inside_indexed_repo`).
- Segredos nunca vão para config, banco ou logs (ver `docs/threat-model.md`).
- Sem telemetria e sem egress: nenhum endpoint existe para receber dados.
