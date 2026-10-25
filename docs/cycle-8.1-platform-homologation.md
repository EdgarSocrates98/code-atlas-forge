# Cycle 8.1 — Homologação de plataforma e browser

**Spec:** `factory/specs/inbox/lf-0149-cycle81-win-linux-homol.md` (Phases 5+6) · **Branch:** `cycle-8.1-ga-readiness`
**Data da medição:** 2026-10-25 · **Máquina Linux:** `edgar-VJFE59F11X-B1011H` — Ubuntu 24.04, kernel `7.0.0-38-generic`, AMD Ryzen 7 5700U (16 threads), 30 GiB RAM
**Toolchain medida:** Python 3.12.3 (`.venv`), pytest 9.1.1, ruff 0.17.2, mypy 2.4.0, Node v22.23.2, Playwright 1.64.1, Angular 22.2.3

> **Caveat honesto — árvore em movimento.** A medição correu em paralelo
> com specs irmãs na mesma branch (lf-0147 Angular 18→22.2.3, lf-0148
> packaging, lf-0150 a11y, lf-0152/0157 impact+release-gates, lf-0153
> security, lf-0154 SBOM, lf-0158 journeys). HEAD evoluiu de `b0e5bb2`
> para `9707987` durante a sessão e a working tree carregou mudanças não
> commitadas desses specs. Cada número abaixo registra o estado medido na
> hora; gates contaminados por interferência de processo foram
> re-executados isoladamente — o mecanismo da interferência está
> documentado em "Achados".

## Matriz de plataforma

| plataforma | superfície | resultado | evidência |
|---|---|---|---|
| **Linux** (esta máquina) | pytest full suite | **1658 passed / 1 skipped** em 125.55s | §1.1 verbatim |
| | `release_check.sh` (14 gates) | run final: **13 PASS / 0 FAIL / 1 SKIP** (e2e por flag — evidência §5.3); runs anteriores documentam falhas transitórias de migração+interferência | §1.2 |
| | CLI smoke em repo real (`requests` OSS) | index+status+search OK — 121 arquivos, 1.484s | §1.3 |
| | `code-atlas index` fixture fresca | OK — `tests/fixtures/sample_repo`, snapshot 1 | §1.4 |
| | latência da API local | p50 1.7–37.2 ms por endpoint | §1.5 |
| **Windows** | CI legs (3 últimas runs de `main`) | **6/6 verdes, 0 flakes**, 14 skips estáveis | §2.1 verbatim |
| | Windows Explorer interativo | **NOT RUN** — sem caixa Windows nesta sessão | declaração |
| | Git Bash (scripts shell) | análise estática + fixes portáteis aplicados; execução real **NOT RUN** | §2.2 |
| **macOS** | — | **NOT RUN** — máquina é Linux; guards BSD aplicados profilaticamente | §3 |

## Matriz de browser (Phase 6)

| browser | engine | spec executada | resultado |
|---|---|---|---|
| Chromium 1248 (Playwright 1.64.1) | Blink | `e2e/cross-browser.spec.ts` (3 testes) | **3/3 PASS** |
| Firefox 1555 (idem) | Gecko | idem | **3/3 PASS** |
| WebKit 2370 (idem) | WebKit | idem | **3/3 PASS** |
| Chromium | suíte e2e completa (195 specs) | todos os specs | **192/195 na corrida isolada**; os 3 fails eram de `journeys.spec.ts` em estado WIP — re-rodado no HEAD final: **8/8 PASS** (suite efetivamente verde) | 
| Chrome/Firefox/Safari interativos (headed, humano) | — | — | **NOT RUN** |
| Safari em macOS real | WebKit nativo | — | **NOT RUN** (sem mac) |
| Firefox/WebKit na suíte completa | — | — | **NOT RUN** (escopo = smoke mínimo por engine) |
| Mobile (iOS/Android browsers) | — | — | **NOT RUN** (fora de escopo do spec) |

---

## 1. Linux — evidência verbatim

### 1.1 pytest full suite (fresh)

Três execuções completas ao longo da sessão (a suíte cresceu com commits
irmãos — packaging, truncation-integrity, security, sbom-freshness):

```
1511 passed, 1 skipped in 89.18s (0:01:29)     # HEAD b0e5bb2, início da sessão
1589 passed, 1 skipped in 96.40s (0:01:36)     # mid-session
1658 passed, 1 skipped in 125.55s (0:02:05)    # HEAD 9707987 — estado final medido
```

Skip único (constante nas 3 corridas):
```
SKIPPED [1] tests/api/test_files_occurrences.py:162: todo arquivo tem ocorrências
```
O teste procura um arquivo sem ocorrências no banco — depende do conteúdo
do fixture; skip acontece em ambas as plataformas (idem no Windows CI).

### 1.2 `scripts/release_check.sh` — resumo verbatim

Três corridas. Run 1 (15:27, árvore no meio do bump Angular 19→20):

```
================ RELEASE GATE SUMMARY ================
  version-changelog-consistency        PASS
  ruff                                 PASS
  mypy                                 PASS
  pytest                               PASS
  build-wheel-sdist                    PASS
  wheel-inventory                      PASS
  clean-install-smoke                  PASS
  cli-smoke                            PASS
  npm-ci                               PASS
  explorer-build                       PASS
  explorer-unit                        FAIL   (13 falhas — LineageComponent specs,
                                             efeito da migração Angular 19 em
                                             andamento; ver §4.3)
  explorer-e2e                         FAIL   ("Cannot find module 'playwright-core'"
                                             — node_modules meio-vazio por `npm ci`
                                             concorrente de spec irmã; ver §4.2)
  npm-audit                            PASS
  pip-audit                            PASS
  RESULT: FAIL — release gate did not pass
```

Run 2 (16:0x, Angular 22.2.3 já commitado, `ATLAS_E2E_PORT=8890`):

```
  explorer-unit                        PASS   (238 specs — fix irmão TestBed.tick)
  explorer-e2e                         FAIL   (179/182 — 3 falhas de artefato:
                                             playwright da spec irmã wipeou
                                             test-results/.playwright-artifacts-*
                                             mid-run; ver §4.2)
  clean-install-smoke                  FAIL   (bug na minha própria alteração de
                                             portabilidade vbin() — resolvia
                                             code-atlas antes do pip install;
                                             corrigido e re-verificado: §4.4)
  demais gates                         PASS
  RESULT: FAIL — release gate did not pass
```

Run 3 — bateria limpa pós-fixes, `ATLAS_E2E_PORT=8890` + `--skip-e2e`
(e2e coberto pela corrida isolada de §5.3 — flag declarada, gate aparece
como NOT RUN honesto):

```
================ RELEASE GATE SUMMARY ================
  version-changelog-consistency        PASS
  ruff                                 PASS
  mypy                                 PASS
  pytest                               PASS
  build-wheel-sdist                    PASS
  wheel-inventory                      PASS
  clean-install-smoke                  PASS   (fix vbin() verificado — §4.4)
  cli-smoke                            PASS
  npm-ci                               PASS
  explorer-build                       PASS
  explorer-unit                        PASS   (238 specs, Chrome Headless 156)
  explorer-e2e                         SKIP    (flag --skip-e2e; evidência §5.3)
  npm-audit                            PASS
  pip-audit                            PASS
-----------------------------------------------------
  version: 1.0.0rc1    FAIL: 0    SKIP(NOT RUN): 1
  RESULT: PASS WITH NOT RUN — skipped gates are listed above;
          a release cut requires a full-battery run (no flags).
  RESULT: PASS
```

### 1.3 CLI smoke em repo real — `requests` (psf/requests, OSS, clone real de `benchmarks/.corpus/realworld/`)

```console
$ code-atlas --version
code-atlas 1.0.0rc1

$ code-atlas doctor
python    ok    3.12.3
sqlite    ok    3.45.1
fts5      ok    available
git       ok    git version 2.43.0
config    ok    <tmp>/config.toml  missing (run `code-atlas init`)
data_dir  ok    <tmp>            writable

$ time code-atlas index benchmarks/.corpus/realworld/requests --json
{"status": "success", "files_indexed": 121, "secrets_skipped": 7,
 "stats": {"files": 121, "files_parsed": 37, "cache_puts": 37,
           "files_failed": 0, "evidence": 3953, "symbols_new": 945,
           "occurrences": 945, "relations": 4061, "edges_skipped": 144,
           "stage_ms_discovery": 93, "stage_ms_hashing": 26,
           "stage_ms_parse": 517, "stage_ms_graph_build": 73,
           "stage_ms_persist_rows": 241, "stage_ms_publish_gc": 52,
           "stage_ms_persist_publish": 298}}
real    0m1.484s

$ code-atlas status --json     # (do root do requests indexado)
{"initialized": true, "schema_version": 12,
 "counts": {"source_files": 121, "snapshots": 1, "index_jobs": 1}}

$ code-atlas search "session" --json   # (cwd = requests/)
{"repository": {"identity_key":
  "git-remote:https://github.com/psf/requests.git"}, ...}
results: 50
```

### 1.4 `code-atlas index` — fixture fresca (`tests/fixtures/sample_repo`, data dir novo)

```json
{"status": "success", "files_indexed": 5, "secrets_skipped": 1, "snapshot_id": 1,
 "stats": {"files_parsed": 2, "evidence": 2, "symbols_new": 9, "relations": 4,
           "stage_ms_discovery": 69, "stage_ms_parse": 1,
           "stage_ms_persist_publish": 3}}
```

### 1.5 Latência da API local (Linux)

Servidor: `code-atlas api --port 8899`, data dir com `requests` indexado
(121 arquivos / 945 símbolos / 3953 evidências). `curl -w '%{time_total}'`,
n=20 por endpoint, loopback:

| endpoint | n | min | p50 | p95 | max (ms) |
|---|---|---|---|---|---|
| `/api/v1/health` | 20 | 1.42 | 1.69 | 1.91 | 2.11 |
| `/api/v1/capabilities` | 20 | 2.71 | 3.35 | 3.75 | 3.76 |
| `/api/v1/repositories` | 20 | 2.66 | 3.04 | 3.50 | 3.67 |
| `/api/v1/index/status` | 20 | 3.68 | 4.28 | 4.62 | 4.92 |
| `/api/v1/diagnostics` | 20 | 5.48 | 6.32 | 6.69 | 107.79* |
| `/api/v1/search?q=session` | 20 | 33.30 | 37.21 | 43.22 | 44.10 |
| `/api/v1/graph/overview?repository_id=1` | 20 | 4.65 | 5.15 | 6.19 | 10.00 |

\* um outlier único de cold-path em `/diagnostics` (primeira chamada); p95
fica em 6.69 ms — sem alçapão de latência no contrato local.

---

## 2. Windows

### 2.1 Evidência de CI — últimas 3 runs de `main` (todas `success`)

Legs `test (windows-latest, py{3.12,3.13})` — runners `windows-2025-vs2026`,
Windows Server 2025 10.0.26100:

| run (id) | commit/título | leg | resultado verbatim |
|---|---|---|---|
| 40218612766 (25/out 17:53Z) | `d5d620a` Cycle 8 merge | win py3.13 | `1498 passed, 14 skipped, 47 warnings in 207.06s` |
| | | win py3.12 | `1498 passed, 14 skipped in 250.01s` |
| 40199628670 (13:52Z) | `5c20177` archive c7 | win py3.12 | `1175 passed, 14 skipped in 145.74s` |
| | | win py3.13 | `1175 passed, 14 skipped, 47 warnings in 127.09s` |
| 40183313325 (10:05Z) | `4bd0690` archive c6.1 | win py3.12 | `541 passed, 14 skipped in 117.72s` |
| | | win py3.13 | `541 passed, 14 skipped, 23 warnings in 114.94s` |

**Flake analysis:** 0 falhas em 6 legs Windows — nenhum padrão de flake;
o conjunto de 14 skips é idêntico nas 3 runs (determinístico). Legs
Windows são ~1.9–3.3× mais lentas que as ubuntu equivalentes (I/O de FS —
comportamento conhecido do runner, não do produto). Esta branch ainda não
foi pushada — não há leg Windows da branch; o delta de suíte (packaging,
security, journeys…) tem skips declarativos e deve se comportar igual, a
confirmar no primeiro CI da branch/PR.

**Skips Windows-específicos** (verbatim, run 40218612766 py3.13):

```
SKIPPED [1] tests\api\test_files_occurrences.py:162: todo arquivo tem ocorrências
SKIPPED [1] tests\discovery\test_scanner.py:118: os.symlink exige privilégio no Windows
SKIPPED [1] tests\discovery\test_scanner.py:136: os.symlink exige privilégio no Windows
SKIPPED [1] tests\discovery\test_scanner.py:145: os.symlink exige privilégio no Windows
SKIPPED [1] tests\discovery\test_symlink_git_mode.py:60:  git + symlinks POSIX
SKIPPED [1] tests\discovery\test_symlink_git_mode.py:72:  git + symlinks POSIX
SKIPPED [1] tests\discovery\test_symlink_git_mode.py:83:  git + symlinks POSIX
SKIPPED [1] tests\discovery\test_symlink_git_mode.py:92:  git + symlinks POSIX
SKIPPED [1] tests\discovery\test_symlink_git_mode.py:102: git + symlinks POSIX
SKIPPED [1] tests\discovery\test_symlink_git_mode.py:112: POSIX only
SKIPPED [1] tests\e2e\test_hardening.py:99: os.symlink exige privilégio no Windows
SKIPPED [1] tests\indexing\test_git_intelligence.py:293: case-rename é POSIX
SKIPPED [1] tests\indexing\test_integrity.py:86: os.symlink exige privilégio no Windows
SKIPPED [1] tests\resolvers\test_lsp_real.py:48: pyright-langserver ausente — real LSP E2E NOT RUN
```

Os `code-atlas` CLI smoke steps do workflow (`--version`, `doctor`,
`index`, `status`, `api --help`, `explorer --help`) executaram com
sucesso nas legs Windows (verbatim no log, saída JSON íntegra).

**Windows Explorer interativo (GUI real):** **NOT RUN** — esta sessão é
Linux; sem caixa Windows disponível. Homologado apenas via CI headless.

### 2.2 Git Bash — auditoria de `scripts/*.sh`

Auditoria estática (máquina Linux — execução em Git Bash real é **NOT
RUN**). Resultados e fixes aplicados:

| construção | onde | estado |
|---|---|---|
| `realpath`, `nproc` | nenhum uso nos dois scripts | nada a fazer |
| `.venv/bin/python` único | ambos os scripts | **bug real p/ Windows** — venv Windows usa `Scripts/python.exe`; corrigido para tentar `bin/` e `Scripts/` + `.exe` |
| fallback `command -v python3` | ambos | **bug real** — Windows típico expõe `python`/`py`, não `python3`; resolução agora tenta `python3 → python → py` |
| `tool()` `-x "$VENV_BIN/$1"` | release_check.sh | `-x` não resolve `.exe`; corrigido (checa `$1` e `$1.exe`) |
| `$TMPVENV/bin/pip`, `…/bin/code-atlas` | release_check.sh (clean-install-smoke) | **bug real** — venv Windows só tem `Scripts/`; novo helper `vbin()` resolve ambos os layouts. *Bug meu de ordem (resolvia antes do install) achado pela própria gate e corrigido — §4.4* |
| `mktemp -d /tmp/…` | release_check.sh:74 | portável para `${TMPDIR:-/tmp}/…` (GNU+BSD+MSYS) |
| `cp -a` | build_release.sh | GNU-only; `cp -Rp` (POSIX, ≡ -a aqui) |
| `sha256sum` | build_release.sh | GNU-only; fallback `shasum -a 256` (macOS) |
| `date -d @epoch` + `date -r` | build_release.sh:73 | **já portável** (fallback GNU→BSD existia) |
| `BASH_SOURCE`, `declare -a`, `pipefail`, `trap EXIT`, `sed -n`, arrays | ambos | bash 3.2+/Git Bash 4.x — OK |
| `npm` (`.cmd` shim), `code-atlas.exe` via PATHEXT | release_check.sh gates 9–14 | MSYS/Git Bash resolve `name→name.exe|name.cmd` em `command -v` e exec — OK por construção, não medido |

Riscos residuais só analisáveis num Windows real (declarados, não medidos):
página de código/console UTF-8 (logs PT-BR com acentos), velocidade de
`npm ci` em NTFS, e comportamento de `mktemp` sob `MSYS2_ARG_CONV_EXCL`.

---

## 3. macOS

**NOT RUN** — esta sessão roda em Linux; sem mac disponível. Em vez de
marcar passivo, as portabilidades BSD foram aplicadas profilaticamente nos
scripts (§2.2: `cp -Rp`, `shasum -a 256`, `mktemp` com `${TMPDIR:-/tmp}`,
e o fallback `date -r` já existente). Cobertura real de macOS fica como
dívida de homologação — o produto é stdlib-Python + shell POSIX, então o
risco residual é baixo, mas não medido.

---

## 4. Achados (findings)

### 4.1 `explorer_dist` empacotado envelhecido sombreia a dist dev → violação CSP cross-browser (ACHADO REAL)

`code-atlas explorer` prefere `src/code_atlas/explorer_dist/` (cópia
empacotada, lf-0148) sobre `explorer/dist/`. Uma cópia staged do build
Angular-19 ainda continha o hack de critical-CSS do Beasties
(`<link rel="stylesheet" … media="print" onload="this.media='all'">`),
que a CSP servida (`script-src 'self'`) bloqueia — **erro de console em
toda página, nos 3 engines** (Chromium/Firefox/WebKit), encontrado pelo
novo `cross-browser.spec.ts`.

Duas faces:
- **Defeito de conteúdo** — resolvido pela spec irmã lf-0147 step-4
  (`inlineCritical: false` + CSP endurecida; descoberta independente).
- **Risco de precedência (permanece)**: `explorer_static_root` prefere a
  cópia empacotada mesmo num checkout dev com `explorer/dist` mais novo —
  servir stale dist em dev é armadilha de homologação. Sugestão para
  revisão fora do meu escopo: preferir `explorer/dist` quando ambos
  existem num checkout git, ou marcar a cópia com build-id.

Para a medição final, `explorer_dist` foi re-staged a partir da dist
vigente (Angular 22.2.3): `rm -rf src/code_atlas/explorer_dist && cp -Rp
explorer/dist/explorer/browser/. src/code_atlas/explorer_dist/` (mesma
receita de `build_release.sh`; dir é gitignored/gerado).

### 4.2 Concurrency hazard: playwright runs paralelos compartilham `explorer/test-results/`

Duas corridas Playwright simultâneas (specs irmãs + a minha) dividem
`test-results/` e `e2e/report`. Playwright apaga `.playwright-artifacts-*`
"stale" no startup da outra corrida → `browserContext.close: ENOENT
…recording*.trace` no meio de testes da corrida viva. Reproduzido 3×
(screenshots, a11y) e confirmado por isolamento: re-run com
`--output=/tmp/…` dedicado → 0 falhas. Recomendação: serializar e2e ou
sempre usar `--output`/`ATLAS_E2E_PORT` dedicados por processo. A config
já suporta `ATLAS_E2E_PORT`; falta cultura de `--output` — registro aqui.

### 4.3 Falhas transitórias de migração Angular (contexto, não defeito meu)

`explorer-unit` falhou 13/238 na run 1 — todas em
`lineage.component.spec.ts` (`Expected 'idle' to be 'offline'`, spy
`getDomainEntities` não chamado): `fixture.whenStable()` não flusheia
effects de signal no Angular 19+; o fix irmão (TestBed.tick,
`eacf06a`) está dentro do escopo lf-0147 e verde na run 2.

### 4.4 Bug introduzido pela minha alteração de portabilidade — achado pela gate

`clean-install-smoke` falhou na run 2 porque o `vbin()` resolvia
`code-atlas` antes de `pip install` rodar. Corrigido reordenando a
resolução para depois do install; re-verificado standalone:
`code-atlas 1.0.0rc1` → `GATE LOGIC OK`, e na run 3 (§1.2a). Registrado
porque é exatamente o tipo de regressão que a gate existe para pegar.

---

## 5. Phase 6 — detalhe do cross-browser

### 5.1 Mudanças

- **`explorer/playwright.config.ts`** — `projects` ganhou legs opt-in
  `firefox`/`webkit` com `testMatch: /cross-browser\.spec\.ts/`, ativadas
  só com `ATLAS_E2E_XBROWSER=1`. Default `npm run e2e`/CI continua
  Chromium-only (contrato de CI preservado: `install --with-deps
  chromium`).
- **`explorer/e2e/cross-browser.spec.ts`** (novo, 3 testes): app carrega
  (shell + nav), `/graph` chega a uma superfície real (canvas sigma ou
  tabela acessível — nunca state-block de erro/offline), `/search` executa
  contra a API real. Cada teste zera `pageerror` + `console.error`.

Browsers instalados localmente — **nenhum download foi necessário**:
Playwright 1.64.1 (resolvido por `@playwright/test@^1.56.1`) pede
chromium-1248 / firefox-1555 / webkit-2370, todos já presentes em
`~/.cache/ms-playwright/`. Nota: um `npx playwright install` durante o
`npm ci` da sessão removeu os browsers 1.56.1-era do cache — em máquina
limpa `npx playwright install` resolve normal.

### 5.2 Resultado verbatim

Run 1 (explorer_dist stale — ACHADO §4.1): `9 failed` — todas falharam
só na asserção `console.error` (violação CSP `script-src`), app renderizou
nos 3 engines.

Run 2 (explorer_dist re-staged da dist Angular-22; port 8890,
`ATLAS_E2E_XBROWSER=1`):

```
  ✓  1 [chromium] app loads — shell renders with main navigation (462ms)
  ✓  2 [chromium] graph route renders a real surface (canvas or table) (599ms)
  ✓  3 [chromium] search executes against the real API without errors (513ms)
  ✓  4 [firefox] app loads — shell renders with main navigation (2.0s)
  ✓  5 [firefox] graph route renders a real surface (canvas or table) (970ms)
  ✓  6 [firefox] search executes against the real API without errors (875ms)
  ✓  7 [webkit] app loads — shell renders with main navigation (967ms)
  ✓  8 [webkit] graph route renders a real surface (canvas or table) (1.3s)
  ✓  9 [webkit] search executes against the real API without errors (1.3s)
  9 passed (12.7s)
```

### 5.3 Suíte e2e completa (Chromium, corrida isolada)

`ATLAS_E2E_PORT=8890 npx playwright test --output=/tmp/atlas-e2e-isolated`
— 195 specs coletados: **192 passed, 3 failed em 15.8m**. Os 3 fails foram
todos em `e2e/journeys.spec.ts` (spec irmão lf-0158 ainda em iteração
naquele instante — J1 timeout 30s, J2 `expected "util.py" got "py"`, J4
fixture de contratos). Re-rodado no HEAD final (`9707987`, spec
commitada "all green"): **8/8 passed (41.5s)** — suíte efetivamente verde
no estado final medido.

Specs irmãs ainda em iteração durante a corrida (e.g. `graph-ceiling` /
`graph-leak-probe`) podem ter sido coletadas ou não conforme o instante —
a contagem oficial da branch é a do próximo CI.

---

## 6. Registro consolidado de NOT RUN

| item | motivo |
|---|---|
| Windows interativo (Explorer GUI, CLI manual) | sem caixa Windows; homologado só via CI |
| Execução real em Git Bash dos scripts | idem — análise estática + fixes aplicados |
| macOS (qualquer superfície) | sem mac; guards BSD aplicados profilaticamente |
| Safari nativo / browsers headed | headless-only nesta sessão |
| Suíte e2e completa em Firefox/WebKit | escopo = smoke mínimo por engine (3 testes) |
| Browsers mobile | fora de escopo do spec |
| Leg Windows **desta branch** | branch ainda não pushada; evidência é de `main` |
| Screen-reader real, Lighthouse em browsers não-Chromium | não homologado em lugar nenhum ainda |

## 7. Conclusão

Linux está homologado com números novos e verbatim (pytest 1658/1, gates
de release, CLI em repo real, latência de API medida). Windows está verde
e estável em CI — 6/6 legs, 14 skips declarativos e constantes, zero
flakes — com a ressalva honesta de que a evidência é de `main`, não desta
branch. Cross-browser: o smoke mínimo passa limpo nos 3 engines e achou um
defeito real de empacotamento/CSP já corrigido por spec irmã. O registro
de NOT RUN acima é o estado honesto — nenhuma lacuna foi disfarçada.
