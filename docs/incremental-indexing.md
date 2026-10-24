# Incremental Indexing — Code Atlas

Como `--incremental` decide o que refazer, o que reusar e como
identidade de repositório e revisão são correlacionadas (lf-0019,
lf-0020, lf-0021). Base formal: ADR-0007 (incremental snapshots).

## Pipeline incremental

1. **Change detector** (`indexing/change_detector.py`): compara o
   manifest do scan atual com o inventário do último READY.
   Classifica cada arquivo `added` / `modified` / `deleted` /
   `renamed` / `unchanged`. Renome é detectado **por igualdade de
   hash de conteúdo** — funciona para `git mv`, rename via filesystem
   e rename só de caixa (onde o FS suporta). Bump de versão de
   analyzer marca o arquivo `modified` (postura conservadora).
2. **Invalidation planner** (`indexing/dependency_planner.py`): fecha
   o conjunto de trabalho — arquivo modificado + importadores
   transitivos (arestas `IMPORTS` do snapshot anterior) — e emite um
   `IndexPlan` inspecionável (`reparse`, `revalidate`, `reuse`).
3. **ParseResult cache** (`indexing/cache.py`): resultados de parse
   serializados em JSON (nunca pickle), versionados por
   parser/grammar/extractor/resolver/builder/config. Cache hit pula
   leitura e Tree-sitter; o builder re-resolve todas as relações
   sobre o universo do snapshot novo — relação nunca é copiada de
   snapshot anterior.
4. **Publicação transacional**: inventário + grafo + evidências +
   cache marks/commit dentro de `BEGIN IMMEDIATE`; READY novo marca
   os anteriores STALE atomicamente.

## Identidade lógica de repositório (lf-0021)

`repository_id` deriva de identidade **lógica**, não do pathname:

| Prioridade | Origem | `identity_key` |
|---|---|---|
| 1 | `remote.origin.url` | `git-remote:<url>` |
| 2 | commit raiz (`rev-list --max-parents=0 HEAD`) | `git-root:<sha>` |
| 3 | path resolvido (sem git) | `path:<root>` |

Consequências:

- **Realocação** (`mv repo/ elsewhere/`): identidade preservada —
  `root_handle` vira campo de diagnóstico atualizado a cada index.
- **Dois checkouts do mesmo remote** compartilham `repository_id`;
  seus snapshots convivem sob o mesmo repo, distinguidos por
  `worktree_digest` e revisão.
- **Sem git** cai para `path:` — a mudança de path cria repo novo
  (sem fatos lógicos para correlacionar).
- Worktrees (`.git` como arquivo), HEAD detached, `.git` ausente e
  históricos com múltiplas raízes (menor sha ordenado) são tratados
  sem crash.

## Snapshot → revisão (dirty binding)

Todo snapshot registra `git_ref`, `commit_sha` e `dirty`:

- `dirty=0`: conteúdo indexado corresponde ao commit `commit_sha`.
- `dirty=1`: worktree tinha alterações não commitadas — o conteúdo
  indexado **diverge** de `commit_sha`. Queries continuam
  snapshot-scoped; a flag impede apresentar conteúdo não commitado
  como se fosse do commit limpo.

Troca de branch produz snapshot com `git_ref`/`commit_sha` do HEAD
novo e `worktree_digest` próprio — resoluções semânticas de uma
revisão nunca vazam para outra (cada snapshot materializa suas
próprias linhas de grafo). Arquivos ignorados são propriedade da
revisão: um path que sai do `.gitignore` numa branch aparece quando
essa branch sai (comportamento correto de `git ls-files`).

## Correlação repo ↔ checkout

A correlação entre o path físico e o repo lógico acontece uma vez por
index, no `get_or_create_repository(identity_key, root_handle)`:

- lookup por `identity_key` (UNIQUE, race-safe via `INSERT OR IGNORE`);
- hit → atualiza `root_handle` se o diretório moveu;
- miss → cria a linha.

Buscas (`repository_id_for`) aceitam `identity_key` **ou**
`root_handle` — compat com bancos pré-v5 indexados por path.
