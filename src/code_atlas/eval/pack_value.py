"""lf-0136 (Cycle 8 Phase 10) — valor medido do context pack (proxy).

**Não é avaliação de agente** — não existe LLM/agent loop no
repositório, então a qualidade-de-resposta é NOT RUN (declarado, não
simulado). O que se mede é um **proxy estrutural honesto**:

- ``no_atlas``: simulação determinística de "engenheiro com grep" —
  busca literal iterativa sobre a árvore da fixture (1 query por
  rodada, tokens = conteúdo dos arquivos abertos);
- ``pack_only``: pack real de ``DomainContextEngine.build_domain``
  (budget default 4000) — ``tokens_payload`` estimado pelo próprio
  engine vs fatos contidos;
- ``atlas_plus_search``: duas variantes reais — ``impact symbol``
  (especialista, 1 query) e ``domains entities``+``lineage`` (montagem
  por busca estrutural — as queries necessárias para juntar a mesma
  informação sem o pack).

Fatos = os mesmos ``ImpactCandidate.qualified_name`` verificados em
lf-0135 — o "o que precisa constar na resposta" rotulado à mão. Um
fato é "answerable" por um braço quando uma de suas formas aparece no
conteúdo que o braço entrega (match com fronteira de token — `orders`
não casa dentro de `enriched_orders`/`staging.orders`).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from code_atlas.context.contracts import DomainPackRequest
from code_atlas.context.domain import DomainContextEngine
from code_atlas.context.tokenizer import HeuristicTokenizer
from code_atlas.eval.impact_truth import (
    index_fixture,
    run_cli,
    run_impact,
)
from code_atlas.impact.domain_traversal import resolve_entity_refs

SCENARIO_SCHEMA = 1
RESULTS_SCHEMA = 1

PACK_BUDGET = 4000           # default do DomainPackRequest
NO_ATLAS_MAX_ROUNDS = 4      # o agente real desiste; cap honesto
_LINEAGE_DEPTH = 8           # teto real do `domains lineage` (MAX_DEPTH)

_DOMAIN_PREFIXES = ("sql:", "pyspark:", "aws-data:", "terraform:",
                    "cicd:", "api:")

def _edge_pattern(token: str) -> re.Pattern[str]:
    """Fronteira para DETECÇÃO DE FATO: o nome não pode estar colado
    em identificador maior (``orders`` não casa em ``enriched_orders``
    nem em ``staging.orders`` — a forma qualificada cobre esse caso).
    ``/`` não é bloqueada no lookbehind: ``x/orders`` termina com o
    nome de verdade."""
    return re.compile(
        r"(?<![\w.\-])" + re.escape(token) + r"(?![\w])")


def _display_of_row(row: Mapping[str, Any]) -> str:
    """``ns.qn[@env]`` — mesma forma de ``_display_of`` do traversal."""
    head = ".".join(
        p for p in (str(row["catalog"] or ""),
                    str(row["namespace"] or "")) if p)
    qn = str(row["qualified_name"])
    disp = f"{head}.{qn}" if head else qn
    env = str(row["environment"] or "")
    return f"{disp}@{env}" if env else disp


def resolve_fact_forms(
    conn: sqlite3.Connection, repository_id: int, fact: str,
) -> tuple[str, ...]:
    """Formas reais do fato — resolvidas em ``domain_entities`` quando
    o fato é de domínio (o ``qualified_name`` de verdade, que é o que
    aparece no código-fonte); fallback heurístico para símbolos."""
    domain = ""
    target = fact
    for prefix in _DOMAIN_PREFIXES:
        if fact.startswith(prefix):
            domain = prefix[:-1]
            target = fact[len(prefix):]
            break
    if not domain:
        return fact_forms(fact)
    for row in conn.execute(
            "SELECT qualified_name, namespace, catalog, environment"
            " FROM domain_entities"
            " WHERE repository_id = ? AND domain = ?",
            (repository_id, domain)).fetchall():
        disp = _display_of_row(row)
        if target == disp or target == str(row["qualified_name"]):
            return _entity_forms(fact, target, disp,
                                 str(row["qualified_name"]))
    return fact_forms(fact)


def _entity_forms(
    fact: str, target: str, disp: str, qn: str,
) -> tuple[str, ...]:
    """Formas de match derivadas da entidade real — ``file#N`` rende
    o caminho (o ``#N`` nunca existe como texto)."""
    forms = {fact, target, disp, disp.split("@", 1)[0], qn,
             qn.split("@", 1)[0]}
    for base in (qn, disp.split("@", 1)[0]):
        if "/" in base:
            forms.add(base.rstrip("/").rsplit("/", 1)[-1])
        if "." in base:
            forms.add(base.rsplit(".", 1)[-1])
        if "#" in base:
            path, _, frag = base.partition("#")
            if path:
                forms.add(path)
                forms.add(path.rsplit("/", 1)[-1])
            if frag:
                forms.add(frag.rsplit("/", 1)[-1])
    return tuple(sorted(f for f in forms if f and not f.isdigit()))


@dataclass(frozen=True)
class PackTask:
    """Tarefa rotulada — pergunta real + conjunto de fatos esperados."""

    task_id: str
    question: str
    fixture: str
    domains: tuple[str, ...]
    cross_domain: bool
    seed: str
    seed_domain: str
    grep_seeds: tuple[str, ...]
    facts: tuple[str, ...]
    notes: str = ""


def load_tasks(path: Path) -> list[PackTask]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if int(raw.get("schema", 0)) != SCENARIO_SCHEMA:
        raise ValueError(f"pack-value tasks schema != {SCENARIO_SCHEMA}")
    out: list[PackTask] = []
    for t in raw["tasks"]:
        out.append(PackTask(
            task_id=str(t["id"]),
            question=str(t["question"]),
            fixture=str(t["fixture"]),
            domains=tuple(str(d) for d in t["domains"]),
            cross_domain=bool(t.get("cross_domain", False)),
            seed=str(t["seed"]),
            seed_domain=str(t.get("seed_domain", "")),
            grep_seeds=tuple(str(g) for g in t.get("grep_seeds", ())),
            facts=tuple(str(f) for f in t["facts"]),
            notes=str(t.get("notes", "")),
        ))
    return out


def validate_task(repo_root: Path, task: PackTask) -> list[str]:
    errs: list[str] = []
    if not (repo_root / task.fixture).is_dir():
        errs.append(f"fixture ausente: {task.fixture}")
    if not task.seed or not task.facts or not task.grep_seeds:
        errs.append("seed/facts/grep_seeds vazios")
    if len(set(task.facts)) != len(task.facts):
        errs.append("facts duplicados")
    return errs


# ---------------------------------------------------------------------
# match de fatos — formas + fronteira de token
# ---------------------------------------------------------------------


def fact_forms(name: str) -> tuple[str, ...]:
    """Formas casáveis de um fato (``sql:staging.orders`` →
    {sql:staging.orders, staging.orders, orders}; ``a/b.py#f`` →
    {a/b.py#f, b.py, f})."""
    s = name
    for prefix in _DOMAIN_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    forms: set[str] = {name, s}
    core = s.split("@", 1)[0]          # '@dev' env / '@L19' linha → cauda
    forms.add(core)
    if "#" in core:
        path, _, frag = core.partition("#")
        if path:
            forms.add(path)
            forms.add(path.rsplit("/", 1)[-1])
        if frag:
            forms.add(frag.rsplit("/", 1)[-1])
    if "/" in core:
        forms.add(core.rstrip("/").rsplit("/", 1)[-1])
    elif "." in core:
        forms.add(core.rsplit(".", 1)[-1])
    return tuple(sorted(f for f in forms if f and not f.isdigit()))


def contains_token(text: str, token: str) -> bool:
    """Match com fronteira — ``token`` não pode estar grudado em
    identificador maior (detecção de fato, não grep)."""
    return _edge_pattern(token).search(text) is not None


def facts_in(
    texts: Sequence[str], forms_map: Mapping[str, Sequence[str]],
) -> list[str]:
    """Fatos casados no conteúdo entregue por um braço."""
    blob = "\n".join(texts)
    return [f for f, forms in forms_map.items()
            if any(contains_token(blob, form) for form in forms)]


# ---------------------------------------------------------------------
# braço A — no_atlas: simulação grep+read iterativa (determinística)
# ---------------------------------------------------------------------


def _iter_fixture_files(fixture_dir: Path) -> dict[str, str]:
    """``relative_path → conteúdo`` de todo arquivo de texto da
    fixture (inclui ``.github/``; binários ilegíveis são pulados)."""
    corpus: dict[str, str] = {}
    for p in sorted(fixture_dir.rglob("*")):
        if not p.is_file():
            continue
        try:
            corpus[str(p.relative_to(fixture_dir))] = \
                p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, ValueError):
            continue
    return corpus


def no_atlas(
    fixture_dir: Path, grep_seeds: Sequence[str],
    forms_map: Mapping[str, Sequence[str]],
    *, max_rounds: int = NO_ATLAS_MAX_ROUNDS,
    counter: HeuristicTokenizer | None = None,
) -> dict[str, Any]:
    """Braço "sem atlas": grep literal → lê arquivos que casam → nomes
    encontrados viram próximas buscas. 1 query por rodada (tokens
    empacotados num único ``grep -e a -e b`` — a busca é substring
    crua, como o grep real); tokens = conteúdo dos arquivos abertos."""
    tok = counter or HeuristicTokenizer()
    corpus = _iter_fixture_files(fixture_dir)
    read: dict[str, str] = {}
    found: set[str] = set()
    used: set[str] = set()
    pending = sorted(set(grep_seeds))
    rounds: list[dict[str, Any]] = []
    queries = 0
    for _ in range(max_rounds + 1):
        if not pending:
            break
        queries += 1
        used.update(pending)
        # grep real é substring crua — sem fronteira de token.
        hits = sorted(
            path for path, text in corpus.items()
            if path not in read and any(
                q in text or q in path for q in pending))
        for h in hits:
            read[h] = corpus[h]
        texts = [*read.values(), *read.keys()]
        now = set(facts_in(texts, forms_map))
        new = now - found
        found |= new
        rounds.append({
            "query_tokens": list(pending),
            "files_matched": len(hits),
            "facts_found_total": len(found),
        })
        pending = sorted(
            {tok for f in new
             for tok in forms_map.get(f, ()) if len(tok) >= 4}
            - used)
    tokens = sum(tok.count(t) for t in read.values())
    missing = [f for f in forms_map if f not in found]
    return {
        "queries": queries,
        "files_read": len(read),
        "files_total": len(corpus),
        "tokens": tokens,
        "facts_found": sorted(found),
        "facts_missing": missing,
        "facts_answerable": len(found),
        "facts_expected": len(forms_map),
        "rounds": rounds,
    }


# ---------------------------------------------------------------------
# braço B — pack-only: DomainContextEngine real
# ---------------------------------------------------------------------


def _dom_key(
    conn: sqlite3.Connection, repository_id: int, task: PackTask,
) -> str | None:
    """``dom:<identity_key>`` da seed — resolvido na base real."""
    rows, _how = resolve_entity_refs(conn, repository_id, task.seed)
    for row in rows:
        if str(row["domain"]) == task.seed_domain:
            return str(row["identity_key"])
    return str(rows[0]["identity_key"]) if rows else None


def pack_arm(
    fixture: Path, task: PackTask, conn: sqlite3.Connection,
    repository_id: int, forms_map: Mapping[str, Sequence[str]],
    *, budget: int = PACK_BUDGET,
) -> dict[str, Any]:
    """Braço "pack-only": um ``build_domain`` real — payload estimado
    pelo próprio engine vs fatos contidos no pack."""
    dom_key = _dom_key(conn, repository_id, task)
    req = DomainPackRequest(
        seed=dom_key or task.seed,
        repository_root=str(fixture),
        budget_tokens=budget)
    pack = DomainContextEngine(conn).build_domain(req)
    section = pack.domain
    text = json.dumps(
        _pack_dict(pack), sort_keys=True, default=str)
    found = facts_in([text], forms_map)
    return {
        "queries": 1,
        "tokens": int(pack.manifest.tokens_payload),
        "tokens_estimated": bool(pack.manifest.tokens_estimated),
        "budget_tokens": budget,
        "status": (str(section.status) if section is not None
                   else "absent"),
        "neighbors_total": (
            int(section.neighbors_total) if section is not None else 0),
        "neighbors_omitted": (
            int(section.neighbors_omitted)
            if section is not None else 0),
        "items_selected": int(pack.manifest.selected),
        "items_omitted": int(pack.manifest.omitted),
        "truncated": bool(pack.manifest.truncated),
        "seed_identity_key": dom_key,
        "facts_found": found,
        "facts_missing": [f for f in task.facts if f not in found],
        "facts_answerable": len(found),
        "facts_expected": len(task.facts),
    }


def _pack_dict(pack: Any) -> dict[str, Any]:
    import dataclasses

    def conv(o: Any) -> Any:
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return {k: conv(v) for k, v in
                    dataclasses.asdict(o).items()}
        if isinstance(o, (tuple, list)):
            return [conv(v) for v in o]
        return o
    out = conv(pack)
    return out if isinstance(out, dict) else {"pack": out}


# ---------------------------------------------------------------------
# braço C — atlas+search: impact (especialista) + montagem por
# domains entities/lineage (busca estrutural genérica)
# ---------------------------------------------------------------------


def _seed_query_term(seed: str) -> str:
    """Termo de ``--q`` natural: basename do caminho (``x.yml#job`` →
    ``x.yml``) ou último segmento (``staging.orders`` → ``orders``)."""
    s = seed
    for prefix in _DOMAIN_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    core = s.split("@", 1)[0].split("#", 1)[0]
    if "/" in core:
        cand = core.rstrip("/").rsplit("/", 1)[-1]
        if cand:
            return cand
    if "." in core:
        return core.rsplit(".", 1)[-1]
    return core


def atlas_search_arm(
    fixture: Path, task: PackTask, conn: sqlite3.Connection,
    repository_id: int, forms_map: Mapping[str, Sequence[str]],
    *, data_dir: Path, config_dir: Path,
    counter: HeuristicTokenizer,
) -> dict[str, Any]:
    """Duas variantes medidas:

    - ``impact``: ``impact symbol <seed> --format json`` — 1 query,
      conjunto verificado completo;
    - ``search``: ``domains entities --q <termo>`` + ``domains
      lineage <dom_key> --direction both --depth 8`` — montagem por
      busca estrutural (só percorre fluxo-de-dados — mede o gap que
      ``CONTAINS``/``REFERENCES``/pontes de símbolo cobrem).
    """
    out: dict[str, Any] = {}

    _run, payload = run_impact(
        fixture, task.seed, data_dir=data_dir, config_dir=config_dir)
    if payload is not None:
        imp_text = json.dumps(payload, sort_keys=True, default=str)
        found = facts_in([imp_text], forms_map)
        out["impact"] = {
            "queries": 1,
            "tokens": counter.count(imp_text),
            "candidates": len(payload.get("impacts") or ()),
            "facts_found": found,
            "facts_answerable": len(found),
        }
    else:
        out["impact"] = {"queries": 1, "error": "sem JSON"}

    queries = 1
    texts: list[str] = []
    run_e = run_cli(
        ("domains", "entities", "--q", _seed_query_term(task.seed),
         "--root", str(fixture), "--json"),
        data_dir=data_dir, config_dir=config_dir)
    texts.append(run_e.stdout)
    dom_key = _dom_key(conn, repository_id, task)
    if dom_key is not None:
        queries += 1
        run_l = run_cli(
            ("domains", "lineage", dom_key,
             "--direction", "both", "--depth", str(_LINEAGE_DEPTH),
             "--root", str(fixture), "--json"),
            data_dir=data_dir, config_dir=config_dir)
        texts.append(run_l.stdout)
    found = facts_in(texts, forms_map)
    out["search"] = {
        "queries": queries,
        "tokens": sum(counter.count(t) for t in texts),
        "facts_found": found,
        "facts_answerable": len(found),
    }
    out["facts_expected"] = len(task.facts)
    return out


# ---------------------------------------------------------------------
# avaliação
# ---------------------------------------------------------------------


def evaluate_task(
    repo_root: Path, task: PackTask, conn: sqlite3.Connection,
    repository_id: int, snapshot_id: int, *, data_dir: Path,
    config_dir: Path,
) -> dict[str, Any]:
    """Mede os três braços de uma tarefa num repo já indexado."""
    fixture = repo_root / task.fixture
    counter = HeuristicTokenizer()
    rep: dict[str, Any] = {
        "id": task.task_id,
        "question": task.question,
        "fixture": task.fixture,
        "seed": task.seed,
        "facts_expected": len(task.facts),
        "notes": task.notes,
    }
    # formas reais dos fatos — resolvidas em ``domain_entities`` deste
    # repositório (o qualified_name de verdade é o que aparece no
    # código fonte e nas saídas dos braços).
    forms_map = {
        f: resolve_fact_forms(conn, repository_id, f)
        for f in task.facts}
    rep["no_atlas"] = no_atlas(
        fixture, task.grep_seeds, forms_map, counter=counter)
    rep["pack_only"] = pack_arm(
        fixture, task, conn, repository_id, forms_map)
    rep["atlas_plus_search"] = atlas_search_arm(
        fixture, task, conn, repository_id, forms_map,
        data_dir=data_dir, config_dir=config_dir, counter=counter)
    rep["target_snapshot_id"] = snapshot_id
    return rep


def _indexed_slot(
    repo_root: Path, task: PackTask, work_dir: Path, ordinal: int,
) -> dict[str, Any]:
    """Provisiona o workspace indexado da fixture (uma indexação real
    por par fixture+adapters) — conn aberta na primeira demanda."""
    ws = work_dir / f"ws-{ordinal}"
    data_dir, config_dir = ws / "data", ws / "cfg"
    data_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    run = index_fixture(
        repo_root / task.fixture, task.domains, task.cross_domain,
        data_dir=data_dir, config_dir=config_dir)
    return {"data_dir": data_dir, "config_dir": config_dir,
            "index_run": run, "conn": None,
            "repo_id": -1, "snapshot_id": -1}


def _open_slot(repo_root: Path, task: PackTask,
               slot: dict[str, Any]) -> None:
    """Abre conn + resolve repo/snapshot pela mesma superfície do
    engine (``StructuralSearch``)."""
    conn = sqlite3.connect(str(slot["data_dir"] / "atlas.db"))
    conn.row_factory = sqlite3.Row
    from code_atlas.config import resolve_root
    from code_atlas.search.structural import StructuralSearch

    search = StructuralSearch(conn)
    repo_id = search.repo_for_root(
        str(resolve_root(str(repo_root / task.fixture))))
    snap = (search.resolve_snapshot(repo_id, None)[0]
            if repo_id is not None else None)
    slot["conn"] = conn
    slot["repo_id"] = repo_id if repo_id is not None else -1
    slot["snapshot_id"] = (
        int(snap.id) if snap is not None and snap.id is not None
        else -1)


def _arm_totals(
    results: Mapping[str, Any], arm: str, sub: str | None,
) -> tuple[int, int, int]:
    """``(queries, tokens, facts)`` agregados de um braço (``sub`` =
    variante dentro de ``atlas_plus_search``)."""
    queries = tokens = facts = 0
    for t in results["tasks"].values():
        if not isinstance(t, dict):
            continue
        block = t.get(arm)
        if sub is not None:
            block = block.get(sub) if isinstance(block, dict) else None
        if not isinstance(block, dict):
            continue
        queries += int(block.get("queries") or 0)
        tokens += int(block.get("tokens") or 0)
        facts += int(block.get("facts_answerable") or 0)
    return queries, tokens, facts


def _summarize(results: dict[str, Any]) -> dict[str, Any]:
    """Agregado honesto — totais por braço; nada de média inventada."""
    facts_total = sum(
        int(t["pack_only"]["facts_expected"])
        for t in results["tasks"].values()
        if isinstance(t, dict)
        and isinstance(t.get("pack_only"), dict)
        and "facts_expected" in t["pack_only"])
    summary: dict[str, Any] = {"tasks": len(results["tasks"]),
                               "facts_total": facts_total}
    for name, arm, sub in (
            ("no_atlas", "no_atlas", None),
            ("pack_only", "pack_only", None),
            ("atlas_impact", "atlas_plus_search", "impact"),
            ("atlas_search", "atlas_plus_search", "search")):
        queries, tokens, facts = _arm_totals(results, arm, sub)
        summary[name] = {
            "queries": queries,
            "tokens": tokens,
            "facts_answerable": facts,
            "fact_coverage": (facts / facts_total
                              if facts_total else None),
        }
    return summary


def evaluate_all(
    repo_root: Path, tasks: Sequence[PackTask], work_dir: Path,
) -> dict[str, Any]:
    """Indexa cada fixture uma vez (mesmo ``atlas.db`` por par
    fixture+adapters) e mede todos os braços de cada tarefa."""
    from code_atlas.env_override import patched_env

    results: dict[str, Any] = {"schema": RESULTS_SCHEMA, "tasks": {}}
    indexed: dict[tuple[str, tuple[str, ...], bool],
                  dict[str, Any]] = {}
    for task in tasks:
        key = (task.fixture, task.domains, task.cross_domain)
        slot = indexed.get(key)
        if slot is None:
            slot = _indexed_slot(repo_root, task, work_dir,
                                 len(indexed))
            indexed[key] = slot
        if slot["index_run"].exit_code != 0:
            results["tasks"][task.task_id] = {
                "id": task.task_id,
                "error": f"index exit {slot['index_run'].exit_code}",
            }
            continue
        if slot["conn"] is None:
            _open_slot(repo_root, task, slot)
        env = {"CODE_ATLAS_DATA_DIR": str(slot["data_dir"]),
               "CODE_ATLAS_CONFIG_DIR": str(slot["config_dir"])}
        with patched_env(env):
            results["tasks"][task.task_id] = evaluate_task(
                repo_root, task, slot["conn"],
                int(slot["repo_id"]), int(slot["snapshot_id"]),
                data_dir=slot["data_dir"],
                config_dir=slot["config_dir"])
    for slot in indexed.values():
        if slot["conn"] is not None:
            slot["conn"].close()

    results["summary"] = _summarize(results)
    return results
