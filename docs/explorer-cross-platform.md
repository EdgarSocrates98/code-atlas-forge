# Explorer — matriz de suporte por plataforma (Cycle 6.1, Phase 14 / lf-0107)

**Data:** 2026-10-25 · **Branch:** `cycle-6.1-explorer-hardening`

Status honesto por plataforma. Convenção:
**VERIFICADO** = executado com evidência citável ·
**CI-COBERTO** = roda em CI a cada PR, não exercido interativamente ·
**NOT RUN** = sem evidência — nada afirmado.

## Resumo

| Plataforma | Wheel + CLI | `code-atlas explorer` (API + SPA servida) | Interativo (browser real) |
|---|---|---|---|
| **Linux** | **VERIFICADO** | **VERIFICADO** | **VERIFICADO** (headless Chromium via Playwright — 66 execuções) |
| **Windows** | **CI-COBERTO** | **CI-COBERTO** (`--help` smoke apenas) | **NOT RUN** |
| **macOS** | NOT RUN | NOT RUN | NOT RUN |

## Linux — o que foi executado

Verificado nesta sessão, na máquina de desenvolvimento (Ubuntu,
kernel `7.0.0-38-generic`, Python 3.12, Node `v22.23.2`):

| Passo | Evidência |
|---|---|
| `python -m build` | **OK** — `dist/code_atlas_forge-0.1.0-py3-none-any.whl` + `code_atlas_forge-0.1.0.tar.gz` presentes |
| Instalação da wheel | coberta pelo job CI `test (ubuntu-latest, py3.12/3.13)` — step `Install wheel (clean)` + `CLI smoke` (`code-atlas --version`, `doctor`, `index`, `status`, `api --help`, `explorer --help`) |
| `code-atlas explorer` servindo API + SPA | **VERIFICADO ao vivo**: `GET /api/v1/health` → `{"status":"ok","service":"code-atlas-api","version":1}`; `GET /` → `200 text/html` (3874 B, o `index.html` compilado); `GET /graph` → `200` (fallback SPA para `index.html`); headers `Content-Security-Policy`, `X-Frame-Options: DENY`, `nosniff`, `no-store`, `no-referrer` confirmados na resposta |
| E2E de browser | Job CI **`explorer-e2e`** (`.github/workflows/ci.yml`, `ubuntu-24.04`, pin comentado no YAML) — **verde**; instala Chromium do Playwright, sobe `code-atlas explorer` real sobre índice real e roda `npm run e2e`. Localmente: `flows.spec.ts` 10 + `flows2.spec.ts` 14 + `security.spec.ts` 8 + `a11y.spec.ts` 29 + `graph-scale.spec.ts` 5 = **66 execuções verdes** |
| Wheel sem Node para o usuário final | **VERIFICADO por construção**: o usuário só precisa de Python — a dist Angular é servida como asset estático pelo `http.server`; Node é exigido apenas para *desenvolver* o Explorer (`npm ci`/`ng build`/`ng test`). Sem a dist, `code-atlas explorer` sobe só a API e avisa em stderr (nunca finge servir UI) |

## Windows — o que é coberto e o que não é

| Item | Status | Evidência |
|---|---|---|
| Suíte Python (ruff/mypy/pytest) | **CI-COBERTO** | job `test (windows-latest, py3.12)` e `test (windows-latest, py3.13)` — `pytest -q --cov` roda a suíte completa (554 testes) no runner Windows |
| Build + instalação da wheel | **CI-COBERTO** | mesmo job — steps `Build wheel`, `Install wheel (clean)`, `CLI smoke` |
| `code-atlas explorer --help` | **CI-COBERTO** | step `CLI smoke` no runner Windows |
| Bind loopback / `webbrowser.open` / porta efêmera | **NOT RUN** | nunca executado interativamente em Windows; desenho é portável (`Path`, bind `127.0.0.1`), mas sem evidência |
| Servir SPA + navegar no Explorer em Windows | **NOT RUN** | não há job de CI com Node/Playwright em `windows-latest`; o job `explorer-e2e` é `ubuntu-24.04` apenas |
| `npm ci` / `ng build` em Windows | **NOT RUN** | nenhum job Node fora de Ubuntu |

**Declaração honesta:** o Explorer *interativo* em Windows é
**NOT RUN** — não "passou". O que existe é cobertura de CI do
pacote Python (wheel, CLI, indexador, `--help` do explorer) em
`windows-latest`. A camada Angular/browser nunca foi exercitada em
Windows. Recomendação até existir evidência: prefira
`python -m code_atlas` (não depende do shim do entry point no PATH) e
reporte falhas de bind/porta/`--open` como bugs — o desenho pretende ser
portável, mas portabilidade interativa do Explorer **não é um claim
verificado**. (Estado anterior registrado em
`docs/explorer-installation.md` §5, que esta tabela confirma e mantém.)

## macOS

**NOT RUN** em todas as camadas — não há job macOS no CI e nenhuma
execução local registrada. Os mesmos motivos de portabilidade do
Windows valem no papel; sem evidência, sem claim.

## Como reproduzir a verificação Linux

```bash
python -m venv .venv && source .venv/bin/activate
pip install .                       # ou a wheel de dist/
code-atlas index tests/fixtures/sample_repo
code-atlas explorer --port 8765 &   # sem --open
curl -s http://127.0.0.1:8765/api/v1/health   # {"status":"ok",...}
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/  # 200 (SPA)
curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8765/graph  # 200 (fallback)
```

## Referências

- CI: `.github/workflows/ci.yml` — jobs `test` (matrix
  `ubuntu-latest`/`windows-latest` × py3.12/3.13), `explorer`,
  `explorer-e2e` (`ubuntu-24.04`), `npm-audit-explorer`, `pip-audit`.
- Instalação do Explorer: `docs/explorer-installation.md` (§5 Windows).
- E2E: `docs/cycle-6-e2e.md`, `docs/explorer-accessibility-report.md`,
  `explorer/e2e/*.spec.ts`.
- Benchmarks em Linux/SwiftShader: `docs/graph-performance-homologation.md`
  e `docs/cycle-6.1-benchmarks.md` — números de GPU não se aplicam a
  outras plataformas.
