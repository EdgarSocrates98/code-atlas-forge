# Instalação — Code Atlas Forge

Requisito: **Python 3.12+**. Sem dependências de runtime (stdlib apenas),
sem Docker, sem conta externa.

## Instalador portátil

A partir de um checkout, a rota recomendada instala o CLI em uma venv
user-scoped e cria um launcher independente do workspace:

```bash
./install.sh              # Linux/macOS/Git Bash
./install.sh --dry-run    # mostra o plano sem escrever
```

No PowerShell use `.\install.ps1`; o mesmo fluxo existe em
`install.windows.sh` para Git Bash. O launcher e o receipt ficam em
`~/.forjas/code-atlas-forge/` (ou no diretório indicado por
`FORGE_INSTALL_HOME`). Adicione o subdiretório `bin/` ao `PATH` uma vez.

> **Divergência deliberada — dois instaladores, dois contratos.**
> `install.sh`/`scripts/install_portable.py` é o bootstrap **portátil**
> (venv user-scoped + launcher); emite recibos `forge-portable-install/1`
> e recusas `FORGE-*`. `code-atlas install` (abaixo) é o **ciclo de vida
> orquestrado** install-2.1 — provisiona data dir/config/registro e emite
> `forge/InstallReceipt/v1`, que é o contrato que o The Forge delega e
> verifica. Os dois coexistem de propósito: um instala o binário, o outro
> provisiona o serviço.

## `code-atlas install` — instalação orquestrada (install-2.1)

O Code Atlas é um **Service Forge** (`installation_kind: "service"` no
`forge.json`): um serviço local de índice/busca — não instala payloads de
host nem perfis de execução. O ciclo de vida provisiona o ambiente local
(data dir, config, registro de repositórios) e grava recibos honestos;
**nunca indexa código** — indexar continua sendo ação explícita
(`code-atlas index`).

```bash
code-atlas install --dry-run            # plano, não escreve nada
code-atlas install -y                   # aplica no repo atual
code-atlas install status               # lê o último recibo global
code-atlas install doctor               # probes reais do ambiente
code-atlas install uninstall -y         # remove o que o recibo prova
code-atlas install uninstall -y --purge # + remove atlas.db/estado indexado
```

### Escopos

| Escopo | Efeito |
|---|---|
| `project` (default) | Um repo root (`--root` > env > cwd): `.code-atlas/` no alvo + registro no índice |
| `workspace` | Descobre membros multi-repo sob `--root` e registra cada um + agrupamento federation |
| `user` | Só o data dir global — nada é escrito em repositório |

`--root` explicito é sempre respeitado como raiz de instalação — nunca
substituído por um ancestral só porque tem marker.

### Descoberta de membros (`--scope workspace`)

Um diretório conta como membro quando carrega um marker: `.git`,
`forge.json`, `forge.agentic.json`, `pyproject.toml` ou `package.json` —
**membros sem git são first-class**. A varredura é determinística e
segura por construção:

- ordenada por nome; raiz com marker conta como membro;
- não segue symlinks nem junctions (links externos não puxam a descoberta
  para fora da raiz);
- pula diretórios ocultos e gerados (`node_modules`, `.venv`, `dist`,
  `target`, caches — ver `IGNORED_DIRS` em `install/workspace.py`);
- profundidade máxima 2 níveis; **nunca desce dentro de um membro já
  descoberto** (repos aninhados mantêm membership isolada — sem
  registro/index duplicado).

Cada membro é registrado com `identity_key` estável (remote git > root
commit > path resolvido) e agrupado num `federation_workspaces` nomeado
pela identidade da raiz. Falha num membro não vira PASS silencioso — o
agregado reflete UNVERIFIED/FAIL.

### Perfis

`minimal` = só `data-dir` · `recommended` = `config` + `data-dir` +
`repository` · `full` = idêntico a `recommended` — não existem
componentes instaláveis adicionais (e indexar não é componente de
install). `--components config,data-dir` seleciona à mão; nomes
desconhecidos (ex.: `skills`, `agents` de orquestradores) viram recusa
tipada `CA-INSTALL-UNSUPPORTED-COMPONENTS` num recibo
`forge/InstallReceipt/v1` com `verification.status=FAIL` — nunca um
traceback exit-2.

### Delegação The Forge (`--host` / `--gateway-profile`)

O contrato de delegação sempre envia `--scope`, `--root`, `--profile`,
`--host` e geralmente `--gateway-profile`. Como Service Forge, o atlas
**aceita e honestamente ignora** os dois últimos: ficam registrados no
recibo como check `NOT_APPLICABLE` e em `extras`, sem efeito operacional.
`forge.json` declara `hosts: []` e `gateway_profiles: []` — nenhum
payload de host, nenhum perfil de execução. Em chamadas delegadas o
recibo JSON sai em stdout mesmo sem `--json` (é o canal que o verificador
do The Forge lê). Aprovação é sempre `--yes`/`-y`.

### Recibos

Todo resultado de install é um envelope `forge/InstallReceipt/v1`:
`schema`, `forge_id`, `operation`, `status`, `verification.status`
(PASS só com checks reais; `dry-run`/recusa/parcial nunca PASS —
UNVERIFIED/FAIL explícitos). Detalhes atlas ficam em extensões (`plan`,
`applied`, `skipped`, `unverified`, `members`, `workspace`, `data_dir`,
`checks`, `extras`).

- **Por alvo**: `<alvo>/.code-atlas/receipts/install-*.json` — a
  convenção `.<state>/receipts` que o The Forge varre. Em workspace, a
  raiz recebe o doc consolidado e cada membro o seu.
- **Global**: `<data-dir>/install/receipt.json` — prova de posse que
  `status`/`uninstall` leem.
- `dry-run` e recusas **não gravam recibo nem estado**.

### Posse e privacidade

Install só escreve dentro do alvo em `<alvo>/.code-atlas/` (recibos +
`.gitignore` gerenciado com payload exato `*\n`). Todo o estado de
serviço — `atlas.db`, índice, config global — vive no XDG data dir
(`CODE_ATLAS_DATA_DIR`), **fora de qualquer repo root**; `--root` é alvo
de instalação, nunca diretório de estado. Nenhum segredo é escrito no
workspace. Uninstall remove apenas o que o recibo prova que o install
criou: arquivos estranhos/de usuário são preservados (`kept`), o
`.gitignore` só sai se o conteúdo for exatamente o gerenciado, e o
workspace federation só sai se o recibo provar que o install o criou e
ele estiver vazio. `--purge` é exigido para apagar `atlas.db`.

### Exemplos

```bash
# plano de workspace sem escrever
code-atlas install --scope workspace --root ~/mono --dry-run --json

# install delegado (como o The Forge chama)
code-atlas install --scope workspace --root ~/mono --profile recommended \
  --host claude-code --gateway-profile default -y --json

# recusa tipada, recibo FAIL — sem traceback
code-atlas install --components skills,agents --json
```

### Troubleshooting

| Sintoma | Causa provável | Caminho |
|---|---|---|
| `status: declined` / `CA-INSTALL-DECLINED` | faltou `-y` | rerodar com `--yes` |
| `CA-INSTALL-UNSUPPORTED-COMPONENTS` | componente fora de `config,data-dir,repository` | ajustar `--components` ou usar `--profile` |
| `verification: UNVERIFIED` | escrita de recibo/registro falhou parcialmente | ver `unverified[]` no recibo; permissões/disco |
| workspace achou 0 membros | raiz sem markers ou tudo ignorado/profundo | `--root` correto; markers dentro de 2 níveis |
| `code-atlas install` abre wizard | TTY sem flags | use flags ou pipe (não-TTY cai no flag path) |

## Linux

```bash
git clone https://github.com/EdgarSocrates98/code-atlas-forge.git
cd code-atlas-forge
python3 -m venv .venv
source .venv/bin/activate
pip install .
code-atlas --version   # code-atlas 1.0.0rc1
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
