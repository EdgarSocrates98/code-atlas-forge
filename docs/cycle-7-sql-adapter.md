# Cycle 7 — SQL domain adapter (lf-0111)

Status: implemented on `cycle-7-domain-intelligence-federation` over the
lf-0110 architecture (`9fab5a9`). Stdlib only — no `sqlparse`, no
`sqlglot`, no new dependency. Nothing is executed: no SQL runs, no jobs
start, no external system is contacted.

## Layout

| File | Role |
|---|---|
| `src/code_atlas/domains/sql/tokens.py` | Tokenizer: words/numbers/strings/idents, operators incl. `->`, `::`, `=>`; comments skipped; `${}`, `{{}}`, `EXEC`-style constructs lexed as `dynamic` tokens |
| `src/code_atlas/domains/sql/nodes.py` | AST dataclasses: `SelectStmt`, `SrcTable/Sub/Join/Group/Values/Unsupported`, `ExprRefs` (cols, stars, subqueries), `Cte`, `SUnsupported` |
| `src/code_atlas/domains/sql/parser.py` | Recursive-descent `_P` parser + `detect_dialect` + `parse_file`; statement-level recovery — a failed statement never poisons siblings |
| `src/code_atlas/domains/sql/extract.py` | AST → `DomainExtractionResult`: entities, relations, column lineage, diagnostics |
| `src/code_atlas/domains/sql/adapter.py` | `SqlDomainAdapter` — contract glue: `supported_artifacts`, content sniffing, per-artifact isolation, evidence |
| `src/code_atlas/domains/sql/__init__.py` | Package exports |

## Parser design

- `parse_file` splits tokens into statements at top-level `;`, parses
  each independently, and resynchronizes on `ParseError` — retained
  statements keep honest numbering and the file still extracts.
- `expr()` is a single bounded scan driven by `_ExprScan` state (paren
  level, CASE depth, lambda-bound identifiers per nesting level,
  recent-name window). It never recurses into subexpressions; inner
  `SELECT`s are collected as `subqueries` for the caller.
- Depth guard `_MAX_DEPTH` propagates through
  `join_chain → primary → _primary_paren → join_chain` so deeply nested
  parenthesized sources raise `ParseError` instead of `RecursionError`.
- `detect_dialect` returns a **label** (`ansi`/`athena`/`spark`) from
  discovered syntax — declared in evidence/discovery metadata, never a
  gate. Divergences are handled inline: `USING`, `LATERAL`, `PIVOT`,
  `UNNEST`, `MERGE` variants, `CLUSTER/DISTRIBUTE/SORT BY`,
  `PARTITIONED BY`, `LOCATION`, `STORED AS`, `QUALIFY`, `PIVOT` etc.
  parse or degrade to `SrcUnsupported`/`SUnsupported` honestly.

## Honesty rules (extract.py)

- **Resolution states.** `resolved` only when the target entity exists
  in the accumulated map; `ambiguous` when several candidates match and
  none can be preferred; `unresolved` when nothing matches;
  `external` for sources declared outside the artifact set. Nothing is
  ever force-resolved.
- **Column lineage** (`_Out`/`_Scope`): qualified refs resolve only to
  a matching source alias; unqualified refs resolve only when exactly
  one source is in scope. `SELECT *` emits table-level lineage plus a
  `column-lineage` limitation diagnostic — never fabricated columns.
  Lambda-bound identifiers (`x -> x + 1`) are excluded from refs.
- **CTEs** expand through `_src_info` with a `seen` set on `id(query)`;
  self-recursion produces a `recursive-cte` diagnostic and a `_DeadSrc`
  (opaque), never a phantom base table. Depth capped by `_SCOPE_DEPTH`.
- **Dynamic/opaque** sources (`${}`, `{{}}`, `EXEC`, `UNNEST`,
  `LATERAL`, `PIVOT`, table functions) yield diagnostics and
  `unresolved`/`unsupported-*` markers — relations are never invented
  for them.
- **Unsupported statements** (`GRANT`, `ALTER`, vendor specifics)
  become `SUnsupported` → diagnostic + safe partial extraction; the
  artifact is not failed wholesale.
- Diagnostics are bounded per file; every emitted entity/relation
  carries `DomainEvidence(extractor="domain:sql",
  adapter_version=...)`.

## Indexing enablement (opt-in)

`IndexCoordinator` gained an optional `domain_registry` parameter.
Default `None` keeps prior behavior byte-for-byte. When provided:

```python
from code_atlas.domains import default_registry
from code_atlas.domains.sql import SqlDomainAdapter

registry = default_registry((SqlDomainAdapter,))
coordinator = IndexCoordinator(conn, domain_registry=registry)
```

- `_extract_domains` runs **outside** the write lock, mirroring parse:
  per enabled adapter it collects scan paths matching
  `supported_artifacts()` (extension `.sql`, glob `*.sql.j2`), builds a
  `DomainExtractionRequest` (path + manifest sha + snapshot digest),
  calls `extract`, and gates the result through
  `validate_extraction`. Crashes and contract violations become
  auditable failure strings — never a FAILED snapshot.
- `_persist_domains` runs inside the same `BEGIN IMMEDIATE` transaction
  as `persist_graph` via `persist_domain_result` — atomic publication,
  rollback covers the whole snapshot.
- Adapter versions enter `_analyzer_versions` as
  `domain:{domain_id}` → registering/enabling/upgrading an adapter
  defeats the digest no-op, exactly like a parser change.
- Counts land in `IndexOutcome.stats` prefixed `domain_<id>_*`
  (`domain_sql_entities`, `domain_sql_relations`, `domain_adapters`,
  `domain_failures`); adapter diagnostics with severity `error` join
  the job `failures` list.
- `.sql` was already classified as language `sql` in
  `discovery/models.py`; the scanner inventories all non-ignored files,
  so SQL artifacts reach the domain phase without parser support.

## Fixture & coverage

`tests/fixtures/sql_warehouse/` models landing → staging → mart:

- `landing/01_landing.sql` — `landing.orders`, `landing.customers`
- `staging/02_staging.sql` — view `staging.v_enriched_orders` (join of
  both landings), CTAS `staging.order_facts` via CTE
- `mart/03_mart.sql` — `mart.daily_revenue` CTAS + `MERGE INTO
  mart.customer_stats` from `staging.order_facts`
- `broken/99_broken.sql` — malformed statements for recovery coverage

Pipeline test asserts 34 entities / 30 relations / 0 diagnostics on the
clean pipeline with correct table- and column-level lineage across
files; the broken file contributes honest `parse-error` diagnostics
while its valid statements still extract.

97 focused tests under `tests/domains/sql/` cover tokenizer, parser,
extraction honesty (ambiguity, `SELECT *`, dynamic/template, recursive
CTE, malformed recovery, unsupported sources), adapter contract
(content sniffing, `*.sql.j2`, hash checks, evidence), fixture
pipeline, persistence dedup, and the opt-in coordinator path
(`IndexOutcome.stats`, persisted `domain_entities`/`domain_relations`).

## Known limits (declared, not hidden)

- `MERGE ... WHEN NOT MATCHED BY SOURCE` delete/update variants parse;
  exotic vendor `MERGE` extensions may degrade to `SUnsupported`.
- `VALUES` sources are literal — `star=True`, no column lineage.
- Correlated subqueries in expressions contribute refs but no source.
- Function-body SQL (`$$ ... $$`) is treated as an opaque string.
- T-SQL / Snowflake-specific grammars (e.g. `TOP`, `PIVOT` xml syntax)
  degrade honestly rather than partially guessing.
