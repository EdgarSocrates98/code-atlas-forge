# Arquitetura do Frontend — Code Atlas Explorer

## Visão geral

`explorer/` é um workspace Angular independente, local-first e offline-first,
que consome a API HTTP local do Code Atlas (`http://127.0.0.1:8741/api/v1`).
Não há telemetria, não há chamadas para serviços externos, e nenhuma
dependência de CDN é carregada em runtime.

Este documento descreve a fundação construída neste ciclo: estrutura de
diretórios, serviços core, roteamento, e decisões técnicas. Ele **não**
cobre telas de produto — essas chegam em ciclos futuros sobre esta base.

## Versões

- Angular: `18.2.13` (standalone components, sem NgModules de feature)
- TypeScript: `5.5.4`, modo `strict` completo
- RxJS: `7.8.1`
- Build: `@angular-devkit/build-angular` com o builder `application` (ESBuild)
- Testes: Karma + Jasmine, `ChromeHeadless`

> Observação de honestidade: as versões acima foram fixadas manualmente no
> `package.json` com base nas versões estáveis conhecidas da linha Angular 18.
> O ambiente de execução usado para montar esta fundação não dispunha de uma
> ferramenta de shell/terminal, portanto `npm install`, `ng build` e `ng test`
> **não foram executados nesta sessão**. Antes de seguir para a próxima
> história, rode localmente:
>
> ```bash
> cd explorer
> npm install
> npm run build
> npm test -- --watch=false
> ```
>
> e corrija qualquer divergência de versão resolvida pelo npm (o lockfile
> gerado nesse primeiro `npm install` é a fonte de verdade subsequente).

## Por que `fetch` em vez de `HttpClient`

A API é estritamente local (loopback, sem autenticação remota, sem CORS
cross-host real). Usar `fetch()` nativo com `AbortController` evita trazer
`HttpClientModule` e seu pipeline de interceptors para um caso de uso que não
precisa dele, mantendo o bundle inicial menor. Cada chamada do `ApiService`:

- cria seu próprio `AbortController` interno;
- aceita opcionalmente um `AbortSignal` externo (encadeado via evento `abort`);
- diferencia três classes de erro: `ApiUnreachableError` (processo local fora
  do ar), `ApiHttpError` (resposta HTTP não-2xx) e `ApiAbortedError`
  (cancelamento esperado, nunca tratado como falha pelo `ErrorHandler`
  global).

Se a complexidade de interceptors (retry, cache, auth) crescer, a migração
para `HttpClient` fica isolada dentro de `core/api/api.service.ts` — nenhum
componente de feature conhece o transporte.

## Estrutura de diretórios

```
explorer/src/app/
  core/
    api/           ApiService, erros tipados, token de configuração da base URL
    models/        Interfaces TypeScript espelhando o contrato HTTP do backend
    state/         ThemeService, RepositoryContextService (Signals)
    errors/        GlobalErrorHandler (boundary de erro de toda a aplicação)
    routing/        (reservado para guards/resolvers futuros)
  shared/
    components/    Componentes de apresentação reutilizáveis (ex.: feature-placeholder)
  features/
    dashboard/      /dashboard
    repositories/    /repositories
    search/          /search
    graph/           /graph
    symbols/         /symbols/:id
    impact/          /impact
    revisions/       /revisions
    context/         /context
    diagnostics/     /diagnostics
    settings/        /settings
  design-system/    tokens.css (ver docs/explorer-design-system.md)
  layout/
    shell/          Casca da aplicação: toolbar, sidebar, router-outlet
```

Cada feature é uma rota lazy (`loadComponent`), carregando um único
componente "standalone" por enquanto. Não há NgModules de feature; o roteador
e o `ApplicationConfig` (`app.config.ts`) são os únicos pontos de composição
globais.

## Roteamento

Definido em `app.routes.ts` com `loadComponent` para cada feature. `''`
redireciona para `/dashboard`; rota curinga (`**`) também redireciona para
`/dashboard`. `withComponentInputBinding()` está habilitado para permitir
que parâmetros de rota futuros (`:id`) cheguem como `@Input()` sem código de
resolver manual.

## Gestão de estado

Sem biblioteca de state management externa, conforme escopo. O estado vive
em:

- **Signals** (`ThemeService`, `RepositoryContextService`,
  `GlobalErrorHandler.lastError`) para estado síncrono local à aplicação;
- **RxJS** fica reservado para fluxos assíncronos compostos (streams de busca,
  debouncing) que ainda não existem nesta fundação — os stubs de feature usam
  `async/await` direto sobre o `ApiService`, que já retorna `Promise`.

## Tratamento de erros

`GlobalErrorHandler` (implementa `ErrorHandler` do Angular) é o único
boundary de erro do processo. Ele:

- ignora `ApiAbortedError` (cancelamento é um fluxo esperado, não uma falha);
- grava o último erro em um `signal` consumido pela tela de Diagnósticos;
- loga no console apenas fora de produção (`ngDevMode`).

Nenhum erro é reportado para um endpoint remoto — não há telemetria.

## Estado "API desconectada"

Todo stub de feature que depende da API tenta uma chamada ao montar e, se o
`ApiService` lançar `ApiUnreachableError`, exibe um estado explícito de "API
local indisponível" em vez de dados falsos (`shared/components/
feature-placeholder`). Isso evita qualquer aparência de dado real quando o
backend não está rodando.

## Orçamento de bundle e produção

`angular.json` define orçamentos (`budgets`) na configuração `production`:
500 KB de aviso / 1 MB de erro para o bundle inicial, 4 KB/8 KB por estilo de
componente. Esses números são um ponto de partida conservador para uma
fundação sem telas pesadas (grafo, etc.) — devem ser revisitados quando a
feature de grafo (provavelmente com uma lib de renderização) for implementada.

## Gaps conhecidos / decisões que precisam de revisão

1. **Execução não verificada nesta sessão.** `npm install`, `ng build --configuration production`
   e `ng test` precisam rodar em um ambiente com shell antes de confiar nesta
   fundação como "verde".
2. Nenhum guard de rota foi implementado (`core/routing/` está vazio além da
   intenção) — não há necessidade de autenticação local, mas um guard de
   "backend indisponível" global poderia substituir a checagem individual em
   cada feature.
3. Seletor de repositório no toolbar é um placeholder somente leitura; não há
   UI de seleção ainda.
4. O arquivo `tokens.css` referencia marcadores de evidência (`--atlas-evidence-*-marker`)
   como valores de string CSS custom property — eles não são renderizados
   diretamente via `content: var(...)` em todos os browsers; o componente
   real de badge de evidência (`shared/components`) deve usar os tokens de
   cor e um ícone/texto explícito no HTML, não confiar no CSS custom property
   para o glifo.
