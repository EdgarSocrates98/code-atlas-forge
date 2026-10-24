# ADR-001: Local-first / offline-first

**Status:** decidida e implementada (Cycle 1)

## Contexto

O Code Atlas deve operar sem Docker, contas SaaS, embeddings remotos, Neo4j,
API keys ou qualquer serviço externo. O Blueprint fixa isso como regra
governante.

## Decisão

Toda funcionalidade implementada opera 100% offline: zero dependências de
runtime além da stdlib, zero imports de rede em `src/` (audit AST), nenhum
dado sai da máquina — telemetria não existe no código.

## Consequências

- Instalação é `pip install .` sem provisionamento.
- O único binário externo consultado é `git` (opcional, com fallback).
- Funcionalidades que exigiriam rede (ex.: resolver pacotes externos)
  ficam explicitamente fora do escopo local.
