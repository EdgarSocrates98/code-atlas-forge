# Troubleshooting

## `init` falha: "config already exists"

O config nunca é sobrescrito sem intenção. Opções:

- `code-atlas init --force` — regrava `config.toml` apontando para o root atual;
- `CODE_ATLAS_CONFIG_DIR=/outro/dir code-atlas init` — isola outra config.

## `doctor` mostra "git: absent"

Não é falha (exit 0): o scanner cai no modo *no-git* — usa `.gitignore`
best-effort e marca o manifest com o modo degradado. Instale git para
classificação tracked/untracked precisa.

## `doctor` mostra "fts5: unavailable"

Falha (exit 1): a build do sqlite3 local não tem FTS5. O schema v2 é pulado
(graceful), mas `status`/`index` funcionam — FTS só será usada no Cycle 4.

## `index` retorna exit 3

Path inválido: não existe, não é diretório, contém `..`, ou é a raiz do
filesystem. Passe caminho absoluto de um diretório real.

## `index` retorna exit 1

Indexação falhou — o snapshot anterior READY é preservado. Diagnostique:

```bash
code-atlas status --root <path>        # snapshot atual e schema_version
sqlite3 ~/.local/share/code-atlas/atlas.db \
  "SELECT id,status,failures FROM index_jobs ORDER BY id DESC LIMIT 3"
```

## Re-index não vira no-op

Digest muda se qualquer arquivo/ignore/segredo mudar **ou** se
`analyzer_versions` mudar (upgrade do scanner). `--full` força re-scan.

## Scan fica "preso" em repo gigante

Limites default: 100k arquivos, 10MiB/arquivo, profundidade 40, sem
timeout. Scan parcial nunca publica READY — é registrado como FAILED.
Para depurar: `CODE_ATLAS_LOG_LEVEL=DEBUG code-atlas index <path>`.

## "database is locked" / writes concorrentes

Dois `code-atlas index` simultâneos no mesmo data_dir serializam via
`BEGIN IMMEDIATE` + busy timeout 5s; o segundo aguarda ou falha com
OperationalError (não corrompe). Readers (`status`) nunca bloqueiam sob WAL.
