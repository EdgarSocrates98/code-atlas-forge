# ADR-014: Segurança de conteúdo — o repo indexado é hostil

**Status:** decidida e implementada (Cycle 1)

## Contexto

O scanner lê código arbitrário que pode conter payloads, symlinks
maliciosos, segredos e entradas patológicas.

## Decisão

Nunca executar/importar código varrido; subprocess confinado a
`gitbin.py` (argv, sem shell, timeout); symlinks nunca atravessados fora da
raiz (`SYMLINK_ESCAPE` warning); limites tipados files/size/depth/timeout;
scan parcial nunca publica READY; somente SQL parametrizado (audit AST);
`..` em PATH rejeitado; logging com redaction.

## Consequências

- `test_scanner_never_executes_repo_code` prova que `setup.py` payload não
  roda durante scan.
- Detalhes e riscos residuais em `docs/threat-model.md`.
