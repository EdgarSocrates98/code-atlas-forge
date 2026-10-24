# ADR-0017 — Decoder SCIP em stdlib

## Status

Aceito (lf-0017, Cycle 3).

## Contexto

Importar índices SCIP exige decodificar protobuf. `protobuf` seria a
primeira dependência runtime do projeto — conflito com o requisito
dependency-free (Blueprint) e aumenta a superfície de supply chain
sobre conteúdo untrusted.

## Decisão

Wire-reader mínimo próprio (`resolvers/scip/decoder.py`): varint,
tags, campos length-delimited, packed ints — o subconjunto do wire
format que `scip.proto` usa. Tipos tipados no `importer.py`; validação
estrutural separada (`validation.py`). Entrada é somente path + cap
de tamanho; conteúdo sempre untrusted.

## Consequências

- Zero dependências novas; decoder auditável (~150 linhas).
- Campo desconhecido é ignorado (forward-compat do wire format).
- Teste proíbe `import protobuf` em qualquer lugar.
