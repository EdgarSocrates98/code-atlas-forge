# Cycle 6.1 — Auditoria de dependências do Explorer (Phase 12, lf-0105)

**Data:** 2026-10-25 · **Branch:** `cycle-6.1-explorer-hardening`
**Método:** execução real nesta sessão — `npm ci` + `npm audit` dentro de
`explorer/` (Node `v22.23.2`, npm com lockfile `explorer/package-lock.json`).
Nada abaixo é estimado: cada número vem da saída do npm nesta máquina.

## 1. Resultado executado

| Comando | Resultado |
|---|---|
| `npm ci` | **OK** — `added 1001 packages, and audited 1002 packages` — lockfile reproduzível (instala limpa a partir do `package-lock.json`, sem mudança no lock) |
| `npm audit` | **55 vulnerabilidades** — 6 low · 20 moderate · 27 high · 2 critical |
| `npm audit --omit=dev` | **8 vulnerabilidades — 4 moderate · 4 high · 0 critical** |
| `npm audit --omit=dev --audit-level=critical` | **exit 0** — gate do CI (`npm-audit-explorer`) passa |
| `npm audit fix` (dry-run, sem `--force`) | **não resolve nada** — toda correção disponível é breaking (`@angular/*@22.x`) ou fora do pin exato `18.2.13` (`@angular-devkit/build-angular@18.2.21`) |

Leitura curta: **nenhuma vulnerabilidade crítica no bundle de produção**.
As 2 críticas vivem na toolchain de build/dev e nunca entram em
`explorer/dist/`. As 4 highs de runtime são advisories do framework
Angular 18 contra superfícies que esta aplicação **não usa** (HttpClient,
TransferCache, SSR, i18n) — ver §3. Correção completa exige major bump
para Angular 22 — decisão adiada, registrada em §5.

## 2. Classificação por camada

| Camada | Vulns | Severidades | Bloqueia? |
|---|---|---|---|
| **Runtime** (dependências de `dependencies` — vai para o bundle) | 8 | 4 high + 4 moderate | **Não** — nenhuma critical; exposição real analisada em §3 |
| **Dev/build toolchain** (`devDependencies` transitivas — `ng build`/`ng test`/`npm ci`/`ng serve`) | 47 | 2 critical + 23 high + 16 moderate + 6 low | **Não** — não sai da máquina de build; mitigação em §4 |

Os 8 de runtime são **todos deps diretas `@angular/*@18.2.13`** (mais as
cadeias "depends on vulnerable versions"): `@angular/common`,
`@angular/compiler`, `@angular/core`, `@angular/router` (high),
`@angular/animations`, `@angular/forms`, `@angular/platform-browser`,
`@angular/platform-browser-dynamic` (moderate, por depender dos anteriores).

As 2 críticas são exclusivamente de dev:

| Pacote | Range | Advisory | Como entra |
|---|---|---|---|
| `piscina` | ≤4.9.3 | GHSA-x9g3-xrwr-cwfg / GHSA-67c8-pqhq-4rmx — prototype-pollution gadget → RCE via `options.filename`/`execArgv` | worker pool do `@angular-devkit/build-angular`/`@angular/build` — só roda durante `ng build` |
| `tar` | ≤7.5.20 | 12 advisories (GHSA-34x7…, hardlink/symlink path traversal, DoS) | `cacache`/`node-gyp`/`pacote` — toolchain de instalação do npm/CLI |

## 3. Exposição real dos advisories de runtime — verificada no código

Os advisories `@angular/*` cobrem superfícies específicas do framework.
Cada um foi conferido contra `explorer/src/app/`:

| Superfície do advisory | Este app usa? | Evidência |
|---|---|---|
| `HttpClient` / `HttpXsrfInterceptor` (XSRF token leakage — GHSA-58c5-g7wp-6w37, GHSA-jfgx-762w-99vr) | **Não** | `ApiService` usa `fetch()` nativo exclusivamente — `api.service.ts:493` (comentário de decisão) e `:737` (a chamada); zero ocorrências de `HttpClient`/`provideHttpClient` em `src/app/**` |
| `HttpTransferCache` / `withRequestsMadeViaParent` (cache-key hashing, credentialed caching — GHSA-39pv-4j6c-2g6v, GHSA-q6f4-qqrg-jv6x, GHSA-jhpw-976m-542j, GHSA-p297-fm68-3q8c) | **Não** | `app.config.ts` provê só `provideZoneChangeDetection` + `provideRouter` + `ErrorHandler` — sem `provideHttpClient`, sem `provideClientHydration`, sem `withHttpTransferCacheOptions` |
| `formatDate` / `digitsInfo` OOM DoS (GHSA-48r7-hpm6-gfxm, GHSA-p3vc-36g9-x9gr) | **Não** | nenhum uso de `formatDate`, pipe `date`, `number`, `currency` ou `percent` em `src/app/**` (datas renderizadas como string crua do payload, ex.: `snap.created_at`) |
| i18n (`$localize`, atributos i18n — GHSA-prjf-86w9-mfqv, GHSA-g93w-mfhg-p222, GHSA-jj27-h5hq-8x99) | **Não** | strings pt-BR literais nos templates; `@angular/localize` ausente do `package.json`; zero `$localize` no fonte |
| SSR / hydration / DOM clobbering (GHSA-rgjc-h3x7-9mwg, router SSR DoS — matrix params, outlet) | **Não** | build `application` gera só bundle de browser (`dist/explorer/browser`); nenhum `@angular/ssr`, nenhum `provideClientHydration`; o "servidor" é o `http.server` stdlib, não Angular Universal |
| Sanitização de template/namespace/host bindings (GHSA-f3m7-gqxr-g87x, GHSA-692r-grfm-v8x7, GHSA-hh8m-fm6v-7cvg, GHSA-58w9-8g37-x9v5, GHSA-jrmj-c5cx-3cw6, GHSA-v4hv-rgfq-gp49) | **Latente** | nenhum `[innerHTML]`, nenhum `bypassSecurityTrust*`, nenhum binding para `src`/SVG/MathML com dado de índice em `src/app/**`. Os advisories descrevem bypasses de sanitizador em binds maliciosos — a exploração exigiria uma sink que hoje não existe no app, mas é vulnerabilidade **real do framework** que só fecha com upgrade |

**Conclusão de exposição:** de ~20 advisories mapeados aos 4 pacotes
high, os que têm vetor endereçável (HttpClient, TransferCache, SSR,
i18n, pipes de formatação) **não se aplicam** — verificado por grep,
não por suposição. Restam os bypasses de sanitização, cuja exploração
dependeria de um caminho de dados host→sink que o app não possui hoje
(dados do índice chegam por interpolação de texto, sanitizada por
default). Ainda assim, ficam como **dívida de framework**: a correção
existe apenas em Angular ≥20 (fix apontado: `22.2.x`).

## 4. Toolchain dev — por que não bloqueia

As 47 vulnerabilidades restantes (incl. as 2 críticas) pertencem à
cadeia `@angular-devkit/build-angular`/`@angular/cli`/Karma/Playwright:
`esbuild`, `vite`, `webpack-dev-server`, `webpack-dev-middleware`,
`rollup`, `postcss`, `picomatch`, `serialize-javascript`, `node-gyp`,
`pacote`, `cacache`, `sigstore`/`tuf-js`, `selfsigned`, `tmp`, `uuid`,
`sockjs`, `webpack`, `tar`, `piscina`, entre outras.

Essas peças executam apenas em **build/test/install na máquina de
desenvolvimento ou CI** — nenhuma é emitida para `explorer/dist/` nem
carregada pelo `code-atlas explorer` em produção (o servidor sirve o
bundle estático já compilado). O modelo de ameaça da toolchain é
"pacote malicioso publicado no npm executando código na máquina de
build" — mitigado por lockfile com integrity hashes (`npm ci` exige o
lock exato) e pelo CI rodando `npm ci` em ambiente efêmero. O vetor
prático restante (ex.: `tar` extraindo tarball malicioso durante
install nativo) exige supply-chain comprometida — risco aceito e
registrado, igual ao do ciclo 6 (L13).

**Correção parcial disponível sem breaking:** bump manual de
`@angular-devkit/build-angular`/`@angular/build`/`@angular/cli`/
`@angular/compiler-cli` de `18.2.13` para `18.2.21` elimina piscina,
postcss, rollup, serialize-javascript, esbuild, vite,
webpack-dev-*, copy-webpack-plugin, picomatch, uuid e outros advisories
de dev — é bump de patch na mesma minor, só fora do pin exato atual
(o `npm audit fix` não o aplica sozinho porque o `package.json` fixa
`18.2.13` sem range). **Decisão: aplicável em follow-up, não aplicado
aqui** (mudança de código, fora do escopo desta fase de documentação).

## 5. O que exige Angular 22 — decisão adiada

Os 8 advisories de runtime só têm fix completo em `@angular/*@22.2.3`
(major bump de 18.2.13 → 22: quebra de API, mínimo de Node/TS, migrações
de template/signals). `npm audit fix --force` instalaria exatamente
isso — rejeitado automaticamente por ser breaking.

**Decisão (deferida, como no ciclo 6 / PR #10):** não fazer upgrade
neste ciclo. Justificativa: (a) zero critical em produção — gate de CI
verde; (b) exposição real analisada em §3 é nula ou latente; (c) um
major bump de framework merece ciclo próprio com regressão visual/E2E.
Quando agendado, o alvo natural é a linha LTS vigente e deve vir com
re-run dos 66 testes Playwright + 160 unit + 5 cenários de benchmark.

## 6. Comandos de verificação

```bash
cd explorer
npm ci                                       # lockfile reproduzível
npm audit                                    # 55 vulns — visão completa
npm audit --omit=dev                         # 8 vulns — só runtime (0 critical)
npm audit --omit=dev --audit-level=critical  # gate do CI — exit 0
npm audit fix --dry-run                      # prova: sem fix não-breaking aplicável
```

Verificação de exposição (o que este app realmente chama):

```bash
grep -rn "HttpClient\|provideHttpClient\|TransferCache" explorer/src/app    # 0 hits
grep -rn "innerHTML\|bypassSecurityTrust\|\$localize" explorer/src/app      # 0 hits
grep -rn "| *date\|| *number" explorer/src/app --include="*.html"           # 0 hits
cat explorer/src/app/app.config.ts                                        # 3 providers only
```

## 7. Referências

- CI gate: `.github/workflows/ci.yml` job `npm-audit-explorer`
  (`npm audit --omit=dev --audit-level=critical`, comentário no YAML
  registra a política critical-gate desde o PR #10).
- Postura de segurança do servidor: `docs/explorer-security-v2.md`.
- Limitação de histórico: `docs/cycle-6-limitations.md` L13/L14
  (L14 — lockfile sem Playwright — **resolvida**: `npm ci` hoje instala
  `@playwright/test` do lock e a suíte roda no CI).
