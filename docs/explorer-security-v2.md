# Explorer — revisão de segurança v2 (Cycle 6.1)

> Atualiza `docs/explorer-security.md` (ciclo 6). O threat model é o
> mesmo; o que muda no 6.1 são os **novos guards** (rate limit, timeout,
> CSP, XFO) e os gaps do ciclo 6 que fecharam. Fonte primária:
> `src/code_atlas/api/server.py` + `src/code_atlas/api/handlers.py`,
> verificados ao vivo nesta sessão (headers observados na resposta real
> de `code-atlas explorer`).

## Threat model (inalterado)

Servidor loopback `127.0.0.1`/`::1` para: a SPA do Explorer no browser
da mesma máquina e ferramentas locais (`curl`, scripts). Explicitamente
**não** para: outras máquinas (sem bind `0.0.0.0` — `unsafe-bind` na
construção), páginas maliciosas em outra aba (DNS-rebinding/CSRF
cross-origin), ou pivô para arquivos arbitrários do disco. Read-only
por contrato (`mode=ro` + `PRAGMA query_only`) limita o blast radius —
o conteúdo do índice (qnames, paths, snippets) continua sensível.

## Guards — estado atual verificado

| Guard | Implementação | Verificado por |
|---|---|---|
| Bind só loopback | `AtlasApiServer.__init__` rejeita host fora de `127.0.0.1`/`localhost`/`::1` → `unsafe-bind` | `server.py:277-279` — code review (recusa não é wire-testável) |
| Allowlist de `Host` | regex `_ALLOWED_HOSTS` → `403 bad-host` (anti DNS-rebinding) | `server.py:45,148-151`; e2e `security.spec.ts` "bad Host header is rejected with 403" |
| Allowlist de `Origin` | `Origin` estrangeira → `403 bad-origin`; **sem** `Origin` → permitido (curl/ferramentas locais) | `server.py:141-146,152-153`; e2e "foreign Origin is rejected" + "request without Origin is allowed" |
| CORS preflight | `OPTIONS` também aplica allowlist antes do 204 | `server.py:202-213`; e2e "CORS preflight with foreign Origin is rejected" |
| Body cap | `MAX_BODY` 64 KiB → `413 payload-too-large` | `server.py:40,155-166`; e2e "oversized POST body is rejected with 413" |
| Response cap | `MAX_RESPONSE` 4 MiB → `500 response-too-large` tipado (nunca stream unbounded) | `server.py:41,120-124,185-186` — **code review only**; nenhum teste provoca >4 MiB (gap declarado desde o ciclo 6, ainda aberto) |
| **Rate limit (NOVO 6.1)** | `_RateLimiter` sliding-window em memória por `client_address[0]`; **300 req/min**, só `/api/v1`, estáticos isentos; excesso → `429 {"error":{"code":"rate-limited"}}` + `Retry-After` | `server.py:42-43,60-79,224-230`; `tests/api/test_api_hardening.py` (janela unitária + 429 real com `rate_limit=3` + isenção de estáticos) |
| **Timeout por conexão (NOVO 6.1)** | `REQUEST_TIMEOUT` 30 s via `settimeout` no `setup`; keep-alive ocioso/leitura travada → encerra quieto; timeout dentro do dispatch → `408 request-timeout` tipado | `server.py:44,102-111,236-239`; `test_api_hardening.py` |
| Static asset traversal | `resolve()` confinado a `static_root`; extensão fora da allowlist → 404; não-arquivo → fallback `index.html` | `server.py:168-193`; e2e "path traversal in static asset path is not served" |
| Sem cookies | nenhum `Set-Cookie` em nenhum caminho | e2e "no Set-Cookie is ever sent" |
| Headers de segurança | `nosniff`, `no-store`, `no-referrer`, **`X-Frame-Options: DENY` (NOVO)**, **`Content-Security-Policy` (NOVO)** em toda resposta | `server.py:82-95`; e2e "security headers present on API responses"; **confirmado ao vivo** via `curl -sD-` nesta sessão |
| Erros tipados | `{"error":{"code","message"}}`; internos → `internal` + mensagem ≤200 chars; nunca traceback | `server.py:134-139` — code review |
| DB read-only | `connect(read_only=True)` + `PRAGMA query_only = ON` por request | `server.py:263-266`, `handlers.py:422-458` — code review (nenhum handler escreve) |

## CSP — a diretiva e o porquê do `unsafe-inline`

```
Content-Security-Policy: default-src 'self'; script-src 'self';
  script-src-attr 'unsafe-inline'; style-src 'self' 'unsafe-inline';
  img-src 'self' data:; connect-src 'self'; font-src 'self';
  frame-ancestors 'none'
```

| Diretiva | Efeito | Nota |
|---|---|---|
| `default-src 'self'` | piso: tudo não-listado só same-origin | — |
| `script-src 'self'` | **bloqueia** `<script>` inline e scripts externos não-self | o bundle é arquivo `.js` servido same-origin — coberto |
| `script-src-attr 'unsafe-inline'` | **permite handlers de evento inline** (`onload=` etc.) | **necessário**: o `index.html` do build Angular 18 carrega o CSS via `<link rel="stylesheet" media="print" onload="this.media='all'">`; sem a diretiva a app renderizava **sem estilo** — achado E2E real (commit `c61ab38`). Trade-off medido: abre só atributos de evento; `<script>` inline segue bloqueado por `script-src 'self'` |
| `style-src 'self' 'unsafe-inline'` | estilos same-origin + atributo `style=` inline | Angular emite styles inline de componentes — requerido |
| `img-src 'self' data:` | imagens locais + data URIs | — |
| `connect-src 'self'` | `fetch` só same-origin | a SPA só fala `/api/v1` same-origin — reforça local-first |
| `font-src 'self'` | fontes locais apenas | zero fontes web por spec |
| `frame-ancestors 'none'` | anti-clickjacking | complementa `X-Frame-Options: DENY` |

## Rate limit — desenho e limite honesto

- **300 req/min por IP cliente, só `/api/v1`.** O valor inicial 120/min
  produzia 429 em sessões legítimas de drill-down (páginas de
  occurrences + fan-out de neighbors); subiu para 300 após medição real
  (commit `c61ab38`) — guard-rail contra loops/runaway, não quota.
- **Chave = IP, não sessão** — em loopback todos os clientes são
  `127.0.0.1`, então na prática o limite é por-máquina (correto para o
  modelo single-user; um segundo consumidor via SSH tunnel divide o
  mesmo teto — degradação declarada em `cycle-6.1-limitations.md` L24).
- Estáticos (`/*` fora de `/api/v1`) são isentos — o burst de assets do
  boot da SPA não conta.

## Gaps abertos (registrados, não corrigidos)

1. **`MAX_RESPONSE` nunca disparado em teste** — o caminho
   `response-too-large` é verificado por leitura; provocá-lo exige
   fixture >4 MiB por resposta (herdado do ciclo 6, ainda aberto — L23).
2. **`script-src-attr 'unsafe-inline'`** é a exceção CSP documentada —
   mantida por necessidade real do bundle Angular 18; revisitar se o
   framework migrar para um carregador sem `onload=` (L25).
3. **Rate limit não diferencia clientes** — por-IP em loopback é
   por-máquina; sem tokens/sessões por design (single-user local).
4. **Advisories npm do Angular 18** — 4 highs de runtime sem vetor
   endereçável neste app (fetch-only, sem HttpClient/SSR/i18n) + 2
   criticals dev-only; análise completa em
   `docs/cycle-6.1-dependency-audit.md`. Fix completo = Angular 22
   (breaking, adiado).
5. `X-Frame-Options: DENY` + `frame-ancestors 'none'` agora existem —
   **fecha o gap #3 do ciclo 6** (clickjacking por iframe same-origin /
   file:// — mitigado pelo deny de framing + allowlist de Host/Origin).

## Verificação

```bash
# headers reais (com o explorer no ar)
curl -sD- -o /dev/null http://127.0.0.1:8741/api/v1/health | \
  grep -i 'content-security\|x-frame\|x-content\|referrer\|cache-control'
# suíte
pytest tests/api -q                                   # hardening + occurrences + API
cd explorer && npx playwright test e2e/security.spec.ts   # 8 casos e2e
```

- `tests/api/` — **29 passed + 1 skipped** (medido nesta sessão):
  `test_api_hardening.py` (janela do limiter, 429+Retry-After, isenção
  de estáticos), `test_files_occurrences.py`, `test_explorer_api.py`.
- `e2e/security.spec.ts` — 8/8 verde no job `explorer-e2e` do CI.
