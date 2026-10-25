# Releasing — política de versionamento e fluxo de release

Spec de origem: `factory/specs/inbox/lf-0142-cycle8-release-eng.md`
(cycle-8, Phase 16). Gates executáveis: `docs/RELEASE-CHECKLIST.md` +
`scripts/release_check.sh`.

## Esquema de versionamento (decisão)

**SemVer 2.0 + PEP 440.** O CHANGELOG já declara SemVer
(`CHANGELOG.md`, cabeçalho); para o pacote Python valem os formas PEP
440 equivalentes:

| momento | versão | tag git | nota |
|---|---|---|---|
| release candidate | `1.0.0rc1`, `1.0.0rc2`, … | `v1.0.0rc1` | SemVer `1.0.0-rc.1` ≡ PEP 440 `1.0.0rc1`; ordena **antes** de `1.0.0` |
| estável | `1.0.0` | `v1.0.0` | gate completo + checklist assinado |
| patch | `1.0.1` | `v1.0.1` | correções retrocompatíveis |
| minor/major | `1.1.0` / `2.0.0` | `v1.1.0` / `v2.0.0` | features retrocompatíveis / breaking |

Contratos versionados do produto (`schema_version` do grafo, `schema`
do context pack, versões de `/api/v1`) são **independentes** da versão
do pacote — mudança de schema não implica major bump por si só (são
namespaces separados), mas toda quebra de contrato público do pacote
(CLI, JSON de saída, comportamento) exige major.

## Onde a versão mora

- **Fonte única**: `src/code_atlas/__init__.py` → `__version__`.
- `pyproject.toml` declara `dynamic = ["version"]` com
  `version = { attr = "code_atlas.__version__" }` — **não editar versão
  no pyproject**; o bump é sempre no `__version__`.
- `explorer/package.json` (`"private": true`) tem campo `version`
  próprio e interno — não publicado, não é fonte de verdade; alinhar
  por cortesia no GA.
- O gate `scripts/release_check.sh` falha se `__version__` divergir
  da seção de release mais recente do CHANGELOG.

## Decisão registrada — primeira versão: `1.0.0rc1`

Contexto: `git tag -l` vazio (2026-10-25) → este é o **primeiro corte
de release** do repositório. Versão anterior: `0.1.0` (nunca taggeada
nem publicada).

Opções consideradas:

1. **Manter `0.1.0`** — rejeitada: `0.1.0` descreve o scaffold do
   Cycle 1, não o produto atual; publicar RC como `0.1.0` quebraria a
   expectativa SemVer de que `0.x` é linha instável em evolução.
2. **`0.9.0rc1`** — rejeitada: um RC de `0.9.0` sinaliza "linha
   convergindo para 0.9.0", não para 1.0. Com o escopo do Blueprint
   (§12, ciclos 1–8) entregue — ciclos 1–7 de features fechados e o
   ciclo 8 sendo Production Quality/hardening (sem escopo funcional
   novo) — um release candidato de 0.9.x subestimaria o estado do
   produto e criaria uma linha `0.9.x` sem propósito.
3. **`1.0.0rc1`** — **escolhida.** O Blueprint (`docs/BLUEPRINT-v1.md`
   §12) declara a matriz completa Cycles 1–8; todo o escopo funcional
   está merged em `main`; a fase de delivery do cycle-8 (lf-0145) é
   explicitamente "RC+PR". `1.0.0rc1` é a forma SemVer/PEP 440 canônica
   de dizer "candidato a 1.0.0": ordena antes de `1.0.0`, permite
   `rc2` se o gate achar regressões, e promove para `1.0.0` sem
   renumeração quando o checklist fechar.

Critério de promoção `rc1 → 1.0.0`: `scripts/release_check.sh` verde
end-to-end + itens manuais do `docs/RELEASE-CHECKLIST.md` + zero
achados P0/P1 abertos na revisão externa (Blueprint §13: "Não marcar
DONE se persistirem falhas P0/P1").

## Fluxo (resumo)

1. Bump `__version__` → seção datada no CHANGELOG → este gate verde.
2. Tag anotada `v<versão>` **somente após** o checklist completo —
   passo humano, nunca automatizado.
3. Publicação PyPI: **não configurada** neste repositório (sem
   workflow de publish; Blueprint §9 prevê "release OIDC quando
   aplicável" — permanece pendente e declarada).

## Decisões de produto deferidas (documentadas, não executadas)

- **Bump de Angular (18.2.13 → 22.x)** — Angular 18 está EOL e
  `npm audit --omit=dev` reporta 8 advisories (4 high, 4 moderate) na
  superfície `@angular/*` (`docs/cycle-8-baseline.md`). O fix exige
  `npm audit fix --force` com bump de 3+ majors — **breaking change e
  decisão de produto separada**, fora do escopo deste RC. Mitigação já
  declarada (PR #10): o app não usa `HttpClient`/`TransferCache`
  (fetch direto), então os advisories de cache/XSRF do HttpClient não
  têm superfície explorável no bundle atual. O gate de release usa
  `--audit-level=critical` (mesmo critério do CI) — as 8 entradas
  high/moderate seguem reportadas e não bloqueiam, por decisão
  registrada. **Recomendação: agendar a migração Angular como item
  pós-1.0 ou pré-GA explícito.**
- ~~**Explorer dist fora do wheel**~~ — **resolvido em lf-0148
  (cycle-8.1, Phase 3)**: o dist Angular é embutido no wheel via
  `code_atlas/explorer_dist/` (package-data) gerado por
  `scripts/build_release.sh`. `pip install` → `code-atlas explorer`
  serve a SPA completa; ver seção "Empacotamento e reprodutibilidade".
- **Classifier PyPI** — `pyproject.toml` declara
  `Development Status :: 3 - Alpha`. Para GA (`1.0.0`) deve ir a
  `5 - Production/Stable`; para a linha RC `4 - Beta` seria o
  correto. Item do checklist de GA.
- **Tag/publish/push** — nunca executados por automação neste repo;
  `scripts/release_check.sh` verifica e reporta, o corte de tag é
  humano.

## Empacotamento e reprodutibilidade (lf-0148, cycle-8.1)

### Explorer dentro do wheel

`scripts/build_release.sh` é o pipeline de artefatos:

1. `npm --prefix explorer ci` + `npm --prefix explorer run build`
   → `explorer/dist/explorer/browser/`;
2. copia para `src/code_atlas/explorer_dist/` (gitignored, gerado —
   entra no pacote via `[tool.setuptools.package-data]`,
   `code_atlas = ["explorer_dist/**"]`);
3. `python -m build` (sdist → wheel a partir do sdist);
4. normaliza mtimes do sdist para `SOURCE_DATE_EPOCH`.

Resolução em runtime (`explorer_static_root` em
`src/code_atlas/api/server.py`): **primeiro**
`importlib.resources.files("code_atlas")/explorer_dist` (wheel
instalado), **fallback** `explorer/dist/explorer/browser` →
`explorer/dist` relativos ao checkout (modo dev). Sem nenhum → API-only
com aviso, comportamento anterior preservado.

Sem `explorer_dist` gerado, `python -m build` produz wheel/sdist
**API-only** — é intencional: a sdist/wheel "pura" continua instalável
e `code-atlas explorer` avisa e serve só a API.

Verificação honesta: `tests/packaging/test_wheel_explorer.py` builda o
wheel numa árvore copiada para tmp, instala em venv limpa
(`--no-deps` — a superfície explorer é stdlib-only) e faz smoke HTTP
contra `code-atlas explorer` real: `/`, `/graph`, `/domains` (SPA
fallback → index.html), asset hashed byte-idêntico ao dist e
`/api/v1/health`. Medido: wheel `1.0.0rc1` com 34 assets do explorer,
208 entradas; todos os endpoints 200.

### Reprodutibilidade (Phase 4)

`SOURCE_DATE_EPOCH` é resolvido por `build_release.sh` assim:
`$SOURCE_DATE_EPOCH` do ambiente → `git log -1 --format=%ct` (timestamp
do HEAD — fixo por commit). Sem git e sem env o script aborta: artefato
não-reproduzível é pior que falha explícita.

Medido neste repo (2026-10-25, `1.0.0rc1`):

- **wheel byte-idêntico**: dois `python -m build` independentes com o
  mesmo `SOURCE_DATE_EPOCH` → sha256 idênticos
  (`c2e90365949542f02c58ec8024849881f7dce9a18a4199ceabee83f803291442`).
  setuptools honra SDE nos timestamps dos zip entries.
- **sdist**: setuptools NÃO honra SDE no sdist — entradas de diretório
  e metadados gerados (`PKG-INFO`, `egg-info/*`, `setup.cfg`) levam o
  relógio real e o header gzip leva o mtime da compressão. Conteúdo
  descomprimido era byte-idêntico; os bytes do `.tar.gz` não. O passo
  de normalização do `build_release.sh` regrava o tar com
  mtime=SDE em todos os membros + `gzip mtime=SDE` → sdists de dois
  builds idênticos têm sha256 iguais
  (`a3b476a27176c8257ff976693601faa700c9316bae9ec5c9b7bf712bf14ae2a8`).
- **camada npm**: assets Angular têm hash de conteúdo no nome; a
  reprodutibilidade do `ng build` é assumida por esses hashes — o
  contrato verificável é o conteúdo do dist copiado, não os nomes.

Instalação via sdist verificada: `pip install dist/*.tar.gz` numa venv
limpa instala `code_atlas/explorer_dist/` (a sdist carrega os assets
quando gerados antes do build) e `code-atlas explorer` serve
`/`, rotas SPA e `/api/v1/health` — medido, não assumido.
