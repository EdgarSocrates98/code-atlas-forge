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

## Arquivo com erro de sintaxe (Cycle 2)

Não é falha: o arquivo vira ocorrência `parsed-with-errors` (ou
`parse-failed` em erro fatal) com evidência `parse-failure:*` e símbolos
parciais que a recuperação da gramática permitir. O snapshot publica
READY normalmente; o job registra o path em `failures`. Verifique com
`code-atlas status` → `counts` e `stats.files_with_errors` no JSON de
`index`.

## `search`/`symbol`/`references`/`graph` retorna exit 1

Causas comuns: repositório nunca indexado (`index` primeiro), snapshot
inexistente (`--snapshot` com id errado) ou snapshot FAILED (última
indexação falhou — rode `status` para ver). O comando sempre consulta o
snapshot READY mais recente por padrão.

## Símbolo "não encontrado" mas o código existe

Checagens: o arquivo tem extensão suportada (`.py`/`.ts`/`.tsx` etc.)?
A ocorrência é de outra linguagem — filtre `--language`. Símbolo em
arquivo `parsed-with-errors` pode ter sido perdido na recuperação de
erro do parser — veja `failures` no job. `symbol` por nome exige
qualified_name **exato** (`pkg.mod.fn`); para substring use `search`.

## Regra do `is not` para nós tree-sitter (dev)

Wrappers `Node` do mesmo nó C **não** compartilham identidade — compare
por `node.id`. Bug real corrigido em lf-0013 (imports fantasma
`pkg.util.pkg.util`).

## Relação `ambiguous`/`unresolved` que "deveria resolver" (Cycle 3)

`ambiguous` = múltiplos candidatos plausíveis (ex.: nome definido
localmente e também importado) — honesto, nunca chutado.
`unresolved` cobre `from m import *` members, barrel reexports TS
(`export {X}`), overloads e alvos fora do snapshot (`external`).
Resolvers opt-in (SCIP/LSP) podem subir a cobertura — ver
`docs/semantic-resolution.md`.

## Resolver SCIP rejeita o índice

`project_root` do `.scip` deve bater com o workspace — binding de
revisão é obrigatório. Oversize → `max_bytes` configurável. Conteúdo
malformado aparece em `LoadOutcome.errors`, não como exceção.

## LSP não resolve nada

Adapters são opt-in e `enabled: false` por default — configure
`[resolvers.lsp.servers.<name>]` com o binário do server **instalado
por você** (o Atlas nunca baixa/instala). Erros aparecem como
`SpawnError`/`LspError` tipados; timeouts são finitos por design.

Diagnóstico primeiro: `code-atlas semantic-status <repo> --json`
mostra por resolver `installed` (artefato presente), `enabled`
(produziria resolver) e `used` (arestas `resolver=*` no snapshot
READY) — distingue "server não instalado" de "nenhuma aresta usada".

## MCP

- `code-atlas mcp doctor --json` — executável no PATH, DB existe,
  schema version, sqlite_vec. Falha comum: `code-atlas` fora do PATH
  → use `python -m code_atlas` no manifest.
- Manifest: `code-atlas mcp manifest --host <host>` **emite** JSON —
  cole manualmente na config do host; Atlas nunca escreve em config
  global (política deliberada).
- Erro `-32602` num tool call = argumento inválido (tipo/limite) —
  o schema do tool está em `tools/list`.
