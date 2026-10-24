# ADR-0018 — Modelo de Validade Incremental

## Status

Aceito (lf-0019 + lf-0020 + lf-0023, Cycle 3).

## Contexto

Incremental precisa decidir o que refazer sem violar isolamento de
snapshots nem reusar fatos que não pode provar idênticos.

## Decisão

- Identidade do arquivo = **conteúdo** (sha256) + idioma + versões
  de analyzer — nunca path sozinho. Sem hash → `modified` (não há
  prova de identidade).
- Reuso só para `ParseResult` (artefato sintático). Relações nunca são
  copiadas: o builder re-resolve tudo sobre o universo do snapshot novo.
- Dependências propagam pelo grafo IMPORTS do snapshot anterior —
  importador de módulo alterado vira `revalidate-relations`.
- Bump de versão classifica conservadoramente: `parser:<lang>` por
  linguagem; `scanner`/`scan_mode` global; `resolver:` revalida
  relações.
- Renome detectado por igualdade de hash — path novo, conteúdo idêntico.

## Consequências

- Corretude verificada: incremental ≡ full rebuild, canonicamente
  (testes + benchmark de cenários).
- `index-plan` expõe o plano sem escrever — decisão inspecionável.
