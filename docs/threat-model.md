# Modelo de Ameaças — Code Atlas Forge (Cycle 1)

> Escopo: o que o Cycle 1 protege hoje. Ciclos futuros (MCP, API remota)
> ampliam a superfície e terão seção própria.

## Ativos

- Conteúdo e metadados do repositório indexado (código, segredos acidentais).
- Integridade do índice local (`atlas.db` no data dir) e dos snapshots READY.
- Host do operador: nada pode ser executado ou vazar da máquina.

## Fronteiras de confiança

| Fronteira | Decisão |
|---|---|
| Conteúdo do repo varrido | **Não confiável** — nunca executado, importado ou instalado (ADR-014). |
| Binário `git` | Confiável até o ponto de invocá-lo com argv fixo, sem shell, timeout 30s, confinado ao módulo `gitbin.py` (audit em teste). |
| FS fora da raiz | Nunca seguido — symlinks que resolvem fora geram warning `SYMLINK_ESCAPE`. |
| Env vars `CODE_ATLAS_*` | Confiáveis para paths; valores nunca logados sem redaction. |
| SQLite local | Único escritor via `BEGIN IMMEDIATE`; readers WAL concorrentes. |

## Entradas controladas por atacante

Um repo indexado pode conter, deliberadamente:

- symlinks para `/etc`, `~/.ssh`, pipes/devices, links circulares;
- arquivos com nomes unicode/NFD vs NFC, espaços, quase-segredos;
- `.gitignore` malformado, UTF-8 inválido, binários, arquivos gigantes;
- `setup.py`/`Makefile` com payloads (jamais executados — teste dedicado);
- profundidade patológica, dezenas de milhares de arquivos (DoS);
- segredos em nomes de arquivo (`.env`, `id_rsa`, `*.pem`, `credentials*`).

## Mitigações implementadas (verificadas por teste)

- **Sem execução:** scanner só lê bytes para SHA-256; `eval`/`exec`/`compile`
  proibidos em `src/` (auditoria AST).
- **Sem egress:** zero imports de `socket`/`urllib`/`requests`/`http`/`asyncio`
  em `src/` (auditoria AST); nenhuma dependência de runtime.
- **Symlink escape:** resolvido contra `root.resolve()`; nunca atravessado;
  symlinks de diretório não são descidos de forma alguma.
- **Segredos:** padrões `.env*`, `*.pem`, `*.key`, `id_*`, `credentials*`
  excluídos de hash e inventário — só contados (`secret_count`).
- **DoS:** limites tipados `max_files`/`max_file_size`/`max_depth`/`timeout_s`
  com warnings; Ctrl-C/timeout → resultado `partial`, nunca READY.
- **Traversal:** `index` rejeita `..` em PATH e a raiz do filesystem (exit 3).
- **SQL injection:** somente queries parametrizadas — auditoria AST reprova
  f-string/concatenação em `execute*`; schema SQL é constante.
- **Estado corrupto:** publicação READY é transação única; falha → rollback +
  snapshot FAILED separado, READY anterior preservado; migrações com checksum
  anti-drift.
- **Log poisoning:** `RedactionFilter` mascara `$HOME`, atribuições de
  token/segredo, blocos PEM e sequências longas tipo chave.

## Riscos residuais (documentados, aceitos no Cycle 1)

- Fallback `.gitignore` sem git é *best-effort* (marcado no modo do scan).
- Arquivo alterado entre scan e hash pode produzir fingerprint de conteúdo
  misto — snapshots são atômicos no DB, não um snapshot do FS.
- `repositories.root_handle` é o path resolvido: mover a pasta cria novo repo
  (limitação conhecida; identidade lógica chega em ciclo posterior).
- Leitor local com acesso ao data dir lê o índice — é o modelo local-first;
  proteção é permissão de FS do SO, não criptografia.
- `index --full` é custoso por design; não há limite de tamanho de corpus além
  dos limites do scanner.

## Verificações relacionadas

`tests/e2e/test_hardening.py` (subprocess confinado, traversal, zero-byte,
oversize, symlink→/etc, profundidade), `tests/storage/test_sql_audit.py`
(SQL estático), `tests/observability/` (redaction), `SECURITY.md` (política).
