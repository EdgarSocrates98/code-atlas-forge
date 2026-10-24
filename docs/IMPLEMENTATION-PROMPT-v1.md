# MASTER PROMPT — CODE ATLAS FORGE (Blueprint v1, Cycle 1 Implementation)

Você é um time virtual sênior de engenharia composto por Principal Software Architect, Staff Python Engineer, Language Tooling Engineer, Graph/Data Engineer, Security Engineer, DevEx Engineer, QA/SDET, AI Context Engineer e Tech Writer. Use os agentes, skills e ferramentas locais pertinentes, se existirem, sem depender de sua presença.

## Contexto obrigatório

Ler `BLUEPRINT-v1.md` como especificação arquitetural normativa. Se o arquivo não estiver no repositório, solicitar ao operador que o disponibilize ou usar a especificação fornecida no prompt — não inventar o conteúdo faltante. O produto Code Atlas Forge é open source, independente, local-first e offline-first; compreende código via análise determinística, grafo tipado, proveniência e context packs futuros. Integrar-se às Forjas é opcional. Não confundir Code Atlas com Forge Doctor, Forge Lens ou The Forge.

## Missão desta execução

Realizar **Cycle 1 — Foundation & Contracts** ponta a ponta, com um vertical slice instalável, CLI, descoberta Git-aware, configuração, contratos de domínio, persistência mínima, testes e CI. Criar backlog/ADRs para Cycles 2–8, mas **não** simular suas funcionalidades nem tentar implementá-las sem base sólida.

Não criar funções placebo. Não declarar testado o que não executou. Não inventar resultados, datas de release, commits ou deploys. Se o workspace for vazio, iniciar projeto novo; se existente, auditar antes de alterar. Preservar código/documentação do usuário e manter mudanças pequenas e revisáveis.

## Fase 0 — Discovery and baseline

1. Inspecionar cwd, `git status`, branch, remotes, arquivos, pyproject, dependências, CI, README, licença e diretórios.
2. Identificar host OS, Python, Git, restrições de instalação; suportar Windows/Linux sem execução remota obrigatória.
3. Produzir resumo de riscos, arquivos a criar/alterar e matriz `exists / missing / partial`.
4. Estudar referências oficiais Tree-sitter, SCIP, MCP, SQLite FTS5 e práticas de segurança; fixar decisões em ADRs sem escolher dependências por moda.
5. Se alguma ferramenta não estiver disponível, registrar limitação e executar máximo possível sem alegar êxito falso.

## Fase 1 — Project scaffold / packaging

1. Criar `pyproject.toml` PEP 621 ou equivalente com CLI via entry point `code-atlas`; evitar dependências extras desnecessárias.
2. Python 3.12+ e `src` layout, módulos boundaries explícitos, type hints, docstrings públicas e mensagens CLI claras.
3. Configurar licença Apache-2.0 proposta, NOTICE conforme aplicável, README pt-BR, CONTRIBUTING, CODE_OF_CONDUCT, SECURITY, changelog e templates GitHub.
4. Usar lint/typecheck/testes (`ruff`, `mypy` ou `pyright`, `pytest`) com versões justificadas; verificar executabilidade em clean venv.
5. Criar `code-atlas --version`, `doctor`, `init`, `status`; help completo e JSON mode onde aplicável.
6. Config localização padrão XDG em Linux e APPDATA/LOCALAPPDATA no Windows, opção `--root`, override via env prefix `CODE_ATLAS_`; não poluir repositório por padrão.
7. Suportar instalação `pip install .` e eventualmente `pipx`; nunca assumir PyPI publicado ou requisitar tokens.

## Fase 2 — Git-aware discovery

1. Implementar scanner seguro de root; `.gitignore` e ignores do Atlas; sem seguir symlink fora root ou diretórios proibidos.
2. Detectar arquivos tracked e untracked de forma configurável, worktrees, branch, HEAD, commit SHA, clean/dirty e git ausente.
3. Identificar extensões e linguagens preliminarmente, tamanhos, sha256 e canonical relative paths.
4. Ignorar segredos, bins, caches, artefatos gerados e diretórios grandes por configuração defensiva.
5. Impor limites de tamanho, volume de arquivos, profundidade, tempo de execução e interrupção segura; expor erros específicos.
6. Não executar build/install/scripts nem código encontrado no repositório.
7. Persistir manifest reproduzível do inventário; suportar arquivos com espaços, Unicode e normalização Windows.

## Fase 3 — Domain contracts + storage minimum

1. Implementar modelos Pydantic/dataclass versionados para `Repository`, `Snapshot`, `SourceFile`, `LogicalSymbol`, `SymbolOccurrence`, `Relation`, `Evidence`, `IndexJob`; distinguir ID lógico de ocorrência.
2. Validar enums e schema de relações (`CONTAINS`, `DEFINES`, `IMPORTS`, `CALLS`, `REFERENCES` etc) e `confidence_class` (`resolved`, `syntactic`, `heuristic`, `inferred`).
3. Definir `resolution_status` e proveniência obrigatória para toda relação publicada.
4. SQLite local: migrações reversíveis quando razoável, WAL, foreign keys, concurrency, `schema_version`, staging/READY/FAILED para snapshot, query parametrizada.
5. Criar versionamento e publicação atômica de inventário de arquivos (sem afirmar parsing simbólico ainda).
6. Suportar reexecução idempotente e erro recuperável sem destruir snapshot `READY` anterior.
7. Definir interfaces `RepositoryScanner`, `ParserAdapter`, `SymbolResolver`, `GraphStore`, `SearchPort`, `IndexCoordinator`; manter parser avançado para Cycle 2.

## Fase 4 — CLI vertical slice

Criar os seguintes comandos **reais**:
- `code-atlas doctor`: mostra runtime/SQLite/FTS5/Git e status de config e dependências opcionais.
- `code-atlas init [--root PATH]`: configura workspace, sem apagar config preexistente sem consentimento.
- `code-atlas status [--json]`: root, branch/commit, index state, file counts, DB migration, versões, warnings.
- `code-atlas index [PATH] [--json] [--full]`: **neste ciclo indexa inventário/fingerprints**, não promete símbolos até Cycle 2.
- `code-atlas --version`, help e códigos de saída consistentes.

Todos devem funcionar sem Claude/Codex/Devin, The Forge, Forge CLI, Docker, LLM ou internet. Não oferecer `search`, `impact` ou `mcp` como operacionais antes da implementação.

## Fase 5 — Security, observability, CI

1. Logs estruturados e redaction; nenhuma cópia de segredos/arquivos pessoais ou código para endpoints externos.
2. Proteção contra path traversal, symlink escape, arquivos enormes, input malformado, race conditions básicas.
3. CI GitHub Actions com testes em Ubuntu + Windows, versões Python suportadas, lint/types/test, build package, `pip install` e CLI smoke.
4. Adicionar security checks de dependências, permissions mínimas GitHub Actions e README do threat model.
5. Criar benchmarks básicos de scan/no-op com corpus sintético e testes de falha.
6. Telemetria desativada por padrão; nenhum serviço externo obrigatório.

## Fase 6 — Test plan

Testar obrigatoriamente:
- empty workspace e git repo básico;
- path com Unicode e espaços, arquivos grandes, binários e malformed UTF-8;
- nested gitignore, ignored folders, arquivos sensíveis;
- git ausente, worktree, branch switch, uncommitted files;
- symlinks fora root, traversal paths, arquivos removidos e renomeados;
- execução concorrente, transação interrompida e recovery;
- no-op indexing determinístico;
- SQLite schema migrations e foreign key integrity;
- CLI help / doctor / init / status / index, install em venv limpo.

Escrever fixtures golden e testes positivos/negativos. Toda falha P0/P1 bloqueia status `DONE`. Registrar testes pulados como `NOT RUN`, nunca como sucesso. Se performance não foi medida, apenas documentar baseline futura.

## Fase 7 — Documentation and roadmap

1. Documentar instalação em Linux/Windows, quickstart, configuração, schemas, ADRs, troubleshooting, arquitetura, threat model, database schema e extensibilidade.
2. Criar `docs/roadmap.md` com Cycles 2–8 e acceptance gates do Blueprint.
3. Documentar limitações reais do Cycle 1 (sem AST, SCIP, grafo completo, impacto, contexto MCP, UI) e não vendê-lo como completo.
4. Criar `docs/benchmark-methodology.md`, matrizes de fixtures, métricas e forma de reproduzir.
5. Registrar `docs/cycle-1-evidence.md` com comandos reais, saída resumida, SO, Python, hash do commit se houver, e blockers.

## Gates de aprovação

G1 Independência: CI/CLI sem qualquer Forge instalada.
G2 Segurança: nenhum código do repo indexado é executado e nenhum dado sai da máquina por padrão.
G3 Determinismo: inventário idêntico produz conteúdo equivalente; no-op sem reprocessamento caro.
G4 Consistência: falha de indexação não corrompe snapshot anterior.
G5 Usabilidade: `init`, `doctor`, `index`, `status` operacionais Windows/Linux.
G6 Contratos: schemas versionados e identidade lógica separada de ocorrência futura.
G7 Qualidade: lint/types/tests/build/smoke verdes, ou blockers explícitos sem falso sucesso.
G8 Documentação: quickstart reproduzível e limitadores conhecidos.

## Entrega e relatório final

Entregar:
1. Resumo executivo do que foi alterado.
2. Árvore de arquivos novos/modificados e justificativas.
3. ADRs e principais trade-offs.
4. Lista de comandos executados e PASS/FAIL/NOT RUN por gate.
5. Testes reais, cobertura e limites de benchmark.
6. Riscos conhecidos com severidade P0/P1/P2 e plano corretivo.
7. Backlog dos Cycles 2–8 com dependências e milestones.
8. Próximo prompt recomendado: Cycle 2 apenas se G1–G8 aprovados.

Se GitHub estiver conectado e o usuário pedir explicitamente alterações, trabalhar em branch e PR; não dar push/merge em main sem autorização. Caso contrário, produzir artefatos e patches locais verificáveis.

**Regra final:** priorizar correção, clareza, segurança e software funcional sobre quantidade de arquivos. Uma implementação menor, auditável e correta é melhor que uma plataforma aparentemente completa com stubs e mocks não sinalizados.
