# Graph Fragment Storage — decisão (lf-0030 / ADR-0020)

> Status: **avaliado e rejeitado** — snapshots completos permanecem o
> modelo. Este doc existe porque a spec do ciclo pedia análise
> explícita de fragmentos; a decisão formal está em
> `docs/adr/adr-0020-incremental-graph-model.md` e a análise completa
> em `docs/incremental-graph-reuse.md`.

## Resumo

Persistência é O(snapshot) (~110k linhas no corpus 5k) — o maior
custo remanescente do incremental (lf-0031). Compartilhar
occurrences/relations entre snapshots foi rejeitado porque:

- relações são fatos sobre o **universo** de nós, não sobre um
  arquivo — compartilhá-las exigiria prova de equivalência de
  universo que custa o próprio rebuild;
- isolamento por snapshot é o que torna `incremental ≡ full`
  canônico *grátis* (testado em `test_incremental`,
  `test_isolation`);
- o gargalo real de Cycle 3 era round-trips por linha — já reduzido
  ~96% por batching (lf-0023) sem tocar o modelo.

O que se faz no lugar: `retain_snapshots` (lf-0031) limita STALE
antigos por repo (opt-in, default ilimitado); `resolver:scip=<sha16>`
na identidade invalida conservadoramente quando o índice externo
muda.

Consequência aceita e documentada: incremental de 1 arquivo em 5k
continua ~4.5s porque reescreve o snapshot inteiro — trade-off de
corretude/auditabilidade assumido no ADR.
