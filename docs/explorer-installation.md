# Instalação e execução do Explorer

> Cycle 6 (lf-0095). Cobre o caminho de usuário final
> (`code-atlas index` → `code-atlas explorer`) e o de desenvolvimento
> (`npm install && npm run build`). Fontes: `src/code_atlas/cli.py`,
> `docs/install.md`, `explorer/package.json`, `explorer/README.md`,
> `docs/cycle-6-e2e.md`.

## 1. Dois runtimes — papel de cada um

| Peça | O que precisa | Quando |
|---|---|---|
| Indexador + API + servidor de assets | **Python 3.12+** e o pacote `code_atlas` (stdlib apenas — nenhuma dependência de runtime, ADR-0009) | **sempre** (execução) |
| SPA Angular 22 compilada | **Node.js + npm** | **somente em build/test** — o navegador consome arquivos estáticos já gerados |

Node **não** é necessário para *usar* o Explorer: quem recebe o pacote já
com `explorer/dist/` compilado roda tudo com Python. Sem a dist, o CLI
degrada honestamente para modo API-only (§3).

## 2. Passo a passo (usuário final)

```bash
# 1. Instalar o pacote Python (ver docs/install.md para variantes)
pip install -e .            # ou .[dev] para ferramentas de desenvolvimento

# 2. Indexar um repositório — cria/atualiza <data_dir>/atlas.db
code-atlas index /caminho/do/repo

# 3. Subir o Explorer (API /api/v1 + assets compilados, se existirem)
code-atlas explorer           # http://127.0.0.1:8741/
code-atlas explorer --open    # idem + abre o navegador
code-atlas explorer --port 9000
```

Notas de comportamento, todas verificadas em `cli.py` (`run_explorer`,
linhas 852–889; parser em 1409–1417):

- **`--open` é opt-in** — o comando nunca abre navegador sem a flag.
- **Bind é sempre loopback** — `--host` aceita `127.0.0.1`/`localhost`/
  `::1`; `AtlasApiServer` rejeita bind público com `unsafe-bind`
  (`server.py:207-209`). `code-atlas explorer` não expõe a máquina à rede.
- **Sem `atlas.db` → erro explícito** — "banco não existe — rode
  `code-atlas index` antes" em stderr + `EXIT_ERROR`; não inicia um
  servidor vazio silenciosamente.
- **Porta ocupada → erro explícito** — `OSError` vira "porta
  indisponível: …" + `EXIT_ERROR`.
- **`--verbose`** — log de requests no stderr.
- `python -m code_atlas` funciona como alias do entry point
  (`src/code_atlas/__main__.py`) — usado pelo harness de E2E.

### Só a API, sem UI

```bash
code-atlas api --port 8741   # mesma superfície /api/v1, sem assets
```

## 3. Fluxo de desenvolvimento (frontend)

```bash
cd explorer
npm install          # instala devDeps Angular 22.2.x + Playwright pin
npm run build        # → explorer/dist/explorer/browser (production,
                     #   budgets: initial warn 500 kB / error 1 MB)
npm run build -- --watch   # ou: npm run watch (configuration development)
code-atlas explorer --open # serve a dist recém-compilada
```

Detalhes importantes:

- **O CLI procura a dist em três origens** (`explorer_static_root` em
  `src/code_atlas/api/server.py`, lf-0148): primeiro
  `code_atlas/explorer_dist/` **empacotado no wheel** (via
  `importlib.resources` — é o que faz `pip install` servir a SPA), e em
  modo dev `explorer/dist/explorer/browser` (saída do
  `@angular/build:application`) e depois
  `explorer/dist`. Só serve se houver `index.html`; senão imprime
  "Explorer não compilado — servindo apenas a API" e sobe só `/api/v1`.
- **`npm start` (`ng serve`)** existe para dev, mas o caminho homologado
  é `code-atlas explorer` sobre a dist compilada — é o que o harness de
  E2E e o usuário final exercem.
- **Testes unitários**: `npm test` — Karma + Jasmine em
  `ChromeHeadlessCI` (`karma.conf.js`, `--no-sandbox --disable-gpu`).
- **`npm ci` exige lockfile consistente**: `npm ci` falha com
  `ERESOLVE` se `package-lock.json` divergir de `package.json` (foi o
  caso durante a migração Angular do cycle-8.1, enquanto o lock era
  regenerado por etapas). `npm install` reconcilia os dois; o lockfile
  comprometido é a fonte canônica para builds reproduzíveis —
  `scripts/build_release.sh` usa `npm ci` de propósito: uma divergência
  deve falhar o release, não ser contornada.

### E2E (Playwright) — quando for executar

```bash
cd explorer
npm install && npx playwright install chromium
npm run build        # a suíte serve a dist real, não ng serve
npm run e2e          # global-setup indexa tests/fixtures/sample_repo
                     # e sobe `code-atlas explorer` real na porta 8743
```

Variáveis: `ATLAS_PYTHON` (interpretador), `ATLAS_E2E_PORT`,
`ATLAS_E2E_BASE_URL`. Detalhes e estado de execução (hoje **NOT RUN**)
em `docs/cycle-6-e2e.md`.

## 4. Onde os dados ficam

O banco `atlas.db` vive no *data dir* da plataforma
(`CODE_ATLAS_DATA_DIR` ou XDG/`%APPDATA%`), **fora** do repositório
indexado — `db_path()` em `cli.py` e `test_db_path_confined_to_data_dir`
(`tests/e2e/test_hardening.py:75-81`). A API abre esse arquivo sempre
read-only.

## 5. Windows — notas honestas

| Item | Estado |
|---|---|
| Python/CLI/indexador em Windows | **PARTIAL** — CI roda a suíte Python em `windows-latest` (Python 3.12/3.13, `.github/workflows/`); teste de symlink é `skipif` no Windows (`test_hardening.py:99-100`) |
| `code-atlas explorer` em Windows | **NOT RUN** — nenhuma evidência de execução manual ou de CI com Node/Angular no Windows; o bind loopback e `webbrowser.open` devem funcionar, mas **não foram verificados** |
| `npm install && npm run build` em Windows | **NOT RUN** — não há job Node na CI; caminhos com `Path(__file__)` são OS-agnósticos no código, sem garantia medida |
| Playwright E2E em Windows | **NOT RUN** — a suíte sequer foi executada em Linux (ver `docs/cycle-6-e2e.md`) |

Recomendação até existir evidência: em Windows, prefira `python -m
code_atlas` (evita depender do shim do entry point no PATH) e reporte
falhas de bind/porta/`--open` como bugs — o desenho pretende ser
portável, mas portabilidade de **Explorer** não é claim verificado.

## 6. Troubleshooting rápido

| Sintoma | Causa provável | Ação |
|---|---|---|
| "banco não existe — rode `code-atlas index` antes" | `atlas.db` ausente no data dir | `code-atlas index <repo>` |
| "Explorer não compilado — servindo apenas a API" | `explorer/dist` sem `index.html` | `cd explorer && npm install && npm run build` |
| "porta indisponível" | 8741 ocupada | `--port <outra>` |
| UI abre mas toda tela diz "API local indisponível" | API fora do ar ou URL não-loopback | manter `code-atlas explorer` rodando; a SPA espera `http://127.0.0.1:8741/api/v1` por padrão |
| `npm ci` falha | lock sem entradas do Playwright | `npm install` (atualiza o lock) |

## 7. Referências

- CLI: `src/code_atlas/cli.py` (`run_explorer` 852–889, `run_api`
  831–849, parser 1401–1417); `src/code_atlas/__main__.py`.
- Servidor: `src/code_atlas/api/server.py` (bind, static, guards).
- Python install: `docs/install.md`. Angular: `explorer/README.md`,
  `explorer/package.json`, `explorer/angular.json`, `explorer/karma.conf.js`.
- E2E: `docs/cycle-6-e2e.md`, `explorer/playwright.config.ts`,
  `explorer/e2e/global-setup.ts`.
