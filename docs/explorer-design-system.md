# Design System — Code Atlas Explorer

Fonte única: `explorer/src/design-system/tokens.css`. Todo componente deve
consumir essas CSS custom properties — nenhum valor de cor, espaçamento ou
tipografia deve ser escrito "cru" em um estilo de componente.

## Temas

Três modos de preferência, gerenciados por `core/state/theme.service.ts`:

- `system` — segue `prefers-color-scheme` do SO, reage a mudanças ao vivo;
- `light` — força claro, define `data-theme="light"` em `<html>`;
- `dark` — força escuro, define `data-theme="dark"` em `<html>`.

A preferência é persistida em `localStorage` sob a chave `atlas-theme`.
Quando a preferência é `system`, o atributo `data-theme` é removido do
`<html>` e o CSS cai no bloco `@media (prefers-color-scheme: dark)` dentro de
`tokens.css`, que só se aplica quando **não** há `[data-theme]` explícito.

Contraste: a paleta clara e escura foi escolhida visando AA (texto normal
≥ 4.5:1, texto grande/UI ≥ 3:1) contra os respectivos fundos `--atlas-color-bg`
e `--atlas-color-bg-subtle`. Isso precisa ser confirmado com uma ferramenta de
contraste automatizada (axe, Lighthouse) assim que houver ambiente de
execução — não foi auditado por ferramenta nesta sessão, apenas escolhido por
proporção de luminância aproximada.

## Categorias de token

| Categoria   | Prefixo                      | Exemplos |
|-------------|-------------------------------|----------|
| Cor         | `--atlas-color-*`             | `--atlas-color-bg`, `--atlas-color-text-muted`, `--atlas-color-accent` |
| Evidência   | `--atlas-evidence-*`          | `--atlas-evidence-verified`, `--atlas-evidence-unresolved-bg` |
| Tipografia  | `--atlas-font-*`              | `--atlas-font-size-md`, `--atlas-font-weight-bold` |
| Espaçamento | `--atlas-space-*`             | escala de 4px: `--atlas-space-1` (4px) até `--atlas-space-8` (64px) |
| Raio        | `--atlas-radius-*`            | `sm` (4px), `md` (8px), `lg` (12px), `full` |
| Sombra      | `--atlas-shadow-*`            | `sm`, `md`, `lg` |
| Movimento   | `--atlas-motion-*`            | `fast` (100ms), `base` (180ms), `slow` (280ms); zeradas sob `prefers-reduced-motion: reduce` |
| Camadas     | `--atlas-z-*`                 | `sticky`, `toolbar`, `overlay`, `modal`, `toast` |

## Sistema de estados de evidência

O domínio do Code Atlas classifica cada aresta de relação (`RelationEdge`)
por `resolution_status`: `verified`, `supported`, `possible`, `unresolved`,
`external`. Esses cinco estados têm:

1. uma cor de destaque (`--atlas-evidence-<estado>`);
2. uma cor de fundo suave (`--atlas-evidence-<estado>-bg`);
3. um marcador textual/glifo (`--atlas-evidence-<estado>-marker`), para
   nunca depender apenas de cor (WCAG 2.2, critério 1.4.1 — uso de cor).

**Regra de implementação obrigatória**: qualquer componente de UI que exiba
um estado de evidência (badge, nó de grafo, linha de tabela) deve renderizar
o marcador e/ou um rótulo textual (`aria-label` ou texto visível) junto da
cor — a cor sozinha nunca é a única pista. O componente real de badge
(`shared/components/evidence-badge`, ainda não implementado neste ciclo) é o
lugar certo para centralizar essa regra.

| Estado       | Cor (claro)  | Cor (escuro) | Marcador | Significado |
|--------------|--------------|--------------|----------|-------------|
| `verified`   | `#1e6b45`    | `#7fd9ab`    | `✓`      | Confirmado por resolução estática completa |
| `supported`  | `#2855d6`    | `#7fa0ff`    | `▲`      | Fortemente indicado, sem prova completa |
| `possible`   | `#8a5a00`    | `#f0c36d`    | `◆`      | Candidato plausível, baixa confiança |
| `unresolved` | `#b3261e`    | `#ff8a80`    | `✕`      | Não foi possível resolver |
| `external`   | `#5a3ea8`    | `#c3a7f5`    | `◇`      | Aponta para fora do repositório indexado |

## Movimento e acessibilidade

- `prefers-reduced-motion: reduce` zera todas as durações de transição/
  animação globalmente via `tokens.css`; componentes não precisam reimplementar
  essa checagem individualmente, desde que usem as variáveis de motion.
- Foco visível: `:focus-visible` global em `styles.css` usa
  `--atlas-color-focus-ring` com contorno de 2px e offset de 2px. Nenhum
  componente deve remover `outline` sem fornecer substituto equivalente.
- Skip link ("Pular para o conteúdo principal") presente na casca
  (`layout/shell`) para navegação por teclado/leitor de tela.

## Gaps conhecidos

- Não existe ainda um componente `evidence-badge` real — apenas o contrato de
  tokens está pronto. A UI de grafo/relações do próximo ciclo deve consumi-lo.
- Auditoria formal de contraste (axe-core, Lighthouse) não foi executada
  nesta sessão por falta de ambiente de execução de shell.
- Tipografia usa apenas fontes de sistema (`ui-sans-serif`, `ui-monospace`)
  — decisão deliberada para manter o app 100% offline, sem fontes
  web/`@font-face` externas.
