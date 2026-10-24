# Agentic Evaluation (Phase 8)

`tests/golden/realworld_eval.py` + `tests/corpus/realworld/`:

- Corpus vendored (commitado) — pyapp WSGI + tsapi TS; offline.
- 8 tarefas, uma por categoria da spec, judgments graduados 0..2.
- Um db por projeto (identity_key herdada do hospedeiro → mesmo
  repo_id por design; isolamento exige banco próprio).
- agent_e2e: `not_run` — sem agente autorizado no ambiente.

Resultados em `docs/cycle-4.1-realworld.md`.
