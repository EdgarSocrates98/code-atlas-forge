# ADR-0010 — Adaptadores de domínio: registro explícito + tabelas dedicadas

- Status: accepted (lf-0110, Cycle 7 Phase 1+2)
- Decisores: spec owner + implementação

Nota de numeração: no Blueprint, "ADR-010" designava a decisão de
renderização do Explorer, que materializou-se como ADR local do
workbench (`explorer/docs/graph-rendering.md`) sem arquivo em
`docs/adr/`. Este arquivo ocupa o slot `adr-0010` do diretório para a
decisão de arquitetura de domínios do Cycle 7.

## Contexto

O Cycle 7 estende o Atlas além do grafo de símbolos: SQL, PySpark,
OpenAPI, Terraform e CI/CD viram **entidades de domínio** com relações
próprias (`READS_FROM`, `PROVISIONS`, `TRIGGERS`…). Três decisões eram
necessárias:

1. **Como adapters entram no sistema** — entry points instalados vs.
   registro explícito;
2. **Identidade canônica** — duas tabelas `orders` em catálogos
   distintos e dois `aws_s3_bucket.logs` em contas/regiões distintas
   não podem colidir;
3. **Onde persistir** — reutilizar `symbols`/`relations` ou schema
   dedicado.

## Decisão

### 1. Registro explícito, entry points opt-in

`DomainRegistry` segue o precedente de `ResolverRegistry` (ADR-0016):
nenhum adapter é ativado por padrão — o caller registra instâncias
explicitamente e controla enable/disable por `domain_id`. Um mecanismo
`discover_entrypoints()` (grupo `code_atlas.domain_adapters`) existe,
mas é **opt-in por chamada**, nunca automático: importar código de
terceiros em tempo de import é incompatível com a postura de segurança
(ADR-0014) e com reprodutibilidade de `analyzer_versions`. Adapters
opcionais nunca são obrigatórios: domínio ausente → `get()` devolve
`None`, `enabled()` o omite, `require()` levanta `UnknownDomainError`.
Domínio desabilitado produz exatamente zero fatos.

### 2. Identidade posicional escapada por segmento

`DomainIdentity` = `dom:<domain>/<repository>/<root>/<catalog>/
<namespace>/<environment>/<qualified_name>/<snapshot>` com cada
segmento percent-encodado (RFC 3986 unreserved, implementação local —
`urllib` é barrado fora de `api/` pela auditoria lf-0086). Posições
fixas + escaping eliminam ambiguidade de separador — um catálogo que
contenha `:` ou `/` não colide com composições de outros segmentos.
A forma **lógica** (sem `snapshot`) é a chave de `domain_entities`;
a ocorrência por snapshot carrega o vínculo temporal, espelhando
`symbols`/`symbol_occurrences`. Capitalização é preservada: política
de case pertence ao adapter (identificadores SQL quoted são
case-sensitive — fundir nomes reais distintos seria desonesto).

### 3. Tabelas dedicadas, migração v9 aditiva

Reutilizar `symbols`/`relations` foi rejeitado por dois motivos
estruturais:

- `relations.type` tem `CHECK` com o vocabulário do Blueprint §5;
  SQLite não altera `CHECK` sem rebuild (create+copy+drop+rename) —
  arriscado e desnecessário;
- o UNIQUE de `symbols` é `(repository_id, language, qualified_name,
  kind)` — `catalog`/`namespace`/`environment` não cabem, e homônimos
  entre catálogos colidiriam de fato.

`domain_entities` (lógica, UNIQUE por `repository_id`+`identity_key`),
`domain_entity_occurrences` (por snapshot, `ON DELETE CASCADE` como o
resto) e `domain_relations` (CHECK próprio = vocabulário de domínio ∪
EdgeKind; endpoints duplos: `*_entity_occ_id` ou `*_symbol_occ_id` — a
ponte código↔domínio de lf-0117/0120 não exige nova migração).
Bancos antigos abrem normalmente e ganham as três tabelas vazias.

Evidência reutiliza `evidence`/`EvidenceRecord` sem mudança — convenção
`extractor="domain:<domain_id>"`. Resolução reutiliza
`ResolutionStatus`/`ConfidenceClass` com invariante dura: `inferred`
nunca é reportada `resolved` (enforçada no dataclass, no validator e
no persist).

## Consequências

- Adapter novo = implementar `DomainAdapter` + `registry.register()`;
  nenhuma mudança em coordinator/persist para declarar o domínio.
- `domain_relations` cresce fora de `relations` — queries cross-domain
  fazem JOIN explícito; o vocabulário de tipos é fechado por CHECK e
  alargá-lo exige rebuild documentado (mesmo trade-off já aceito no v1).
- `inferred → resolved` é impossível por construção nos três níveis
  (modelo, validação, persistência).
- Snapshot isolation preservado: ocorrências e relações de domínio
  caem por `ON DELETE CASCADE` com o snapshot; auditoria
  `cross_snapshot_domain_relations` espelha a de `relations`.
