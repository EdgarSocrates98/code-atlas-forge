"""Verdade-terreno independente para adapters de domínio (lf-0129, Cycle 8).

Complemento do harness golden (lf-0124): os goldens são inventários
curados do comportamento *observado*; os truth sets em
``evaluation/ground_truth/<domain>.json`` são rótulos **manuais**
escritos a partir da leitura direta de cada arquivo da fixture —
não derivados da saída do adapter. O diff rótulo↔extração produz
``TRUE_POSITIVE`` / ``FALSE_POSITIVE`` / ``FALSE_NEGATIVE`` por classe
(entidade, relação, diagnóstico) e precision/recall/F1 por domínio.

Schema do truth set (``schema: 1``)::

    {
      "schema": 1, "domain": "sql", "fixture": "tests/fixtures/...",
      "entities": {
        "complete": true|false,          # true = lista exaustiva rotulada
        "expected":  [ <regra-entidade>, ... ],
        "forbidden": [ <regra-entidade>, ... ]
      },
      "relations": { "complete": ..., "expected": [...], "forbidden": [...] },
      "diagnostics_expected": [ <regra-diag>, ... ],
      "patterns_forbidden":    [ {"substr": "...", "note": "..."}, ... ]
    }

Semântica de casamento (por regra — todos os campos presentes devem
casar; ausente = não filtra):

- ``<campo>``           valor exato (``name``, ``type``, ``from``, ``to``,
  ``code``, ``path``, ``resolver``, ``severity``);
- ``<campo>_prefix``    prefixo do valor renderizado;
- ``<campo>_contains``  substring do valor renderizado;
- ``status``/``confidence``  string ou lista de strings (qualquer uma);
- ``kind``              ``domain_entities.kind`` persistido;
- ``count``             (expected) exige exatamente N matches — a mais
  vira FP (over-extraction), a menos vira FN;
- ``max_count``         (forbidden) só os matches além de N viram FP —
  para casos "até N identidades legítimas" (mesma tabela, contas
  distintas);
- ``min_count``         (diagnostics) exige ≥N diagnósticos casando.

Contabilização por classe:

- ``TP`` = itens extraídos cobertos por regras expected (dedup por
  item) e não marcados como fabricação/kind-mismatch;
- ``FN`` = fatos rotulados ausentes (regra sem match ou shortfall de
  ``count``);
- ``FP`` = itens extraídos casando regras forbidden + excedentes de
  ``count`` + (``complete``) qualquer item não coberto por expected —
  fabricação real, listada item a item;
- ``unaudited`` = itens fora de todo rótulo (classe não ``complete``) —
  reportados como transparência, nunca pontuados;
- conflito expected∩forbidden = erro de rotulagem — o item conta FP e
  é listado em ``conflicts``.

``patterns_forbidden`` varre nomes/qualified_name/atributos de
entidades e ocorrências + endpoints de arestas + diagnósticos — cada
ocorrência do substring vira um FP de classe ``patterns`` (vazamento de
segredo/dado sensível é fabricação de conteúdo).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

TRUTH_SCHEMA = 1

#: campos textuais de aresta com trio exact/prefix/contains
_EDGE_TEXT_FIELDS = ("type", "from", "to", "resolver")
#: campos de aresta com valor único ou lista de aceitos
_EDGE_CHOICE_FIELDS = ("status", "confidence")
#: campos textuais de diagnóstico
_DIAG_TEXT_FIELDS = ("code", "path")


def _text_ok(rule: Mapping[str, Any], field_name: str, value: str) -> bool:
    """Trio exact/prefix/contains — todos os presentes devem casar."""
    exact = rule.get(field_name)
    if exact is not None and value != str(exact):
        return False
    prefix = rule.get(f"{field_name}_prefix")
    if prefix is not None and not value.startswith(str(prefix)):
        return False
    contains = rule.get(f"{field_name}_contains")
    if contains is None:
        return True
    if isinstance(contains, str):
        return contains in value
    # lista: TODOS os substrings precisam aparecer no valor
    return all(str(c) in value for c in contains)


def _choice_ok(rule: Mapping[str, Any], field_name: str, value: str) -> bool:
    """Campo escalar ou lista-de-aceitos; ausente = não filtra."""
    want = rule.get(field_name)
    if want is None:
        return True
    if isinstance(want, str):
        return value == want
    if isinstance(want, Sequence):
        return value in {str(w) for w in want}
    return False


def entity_rule_matches(rule: Mapping[str, Any], name: str, kind_of: Callable[[str], str]) -> bool:
    """``name`` casa o trio textual; ``kind`` compara o kind persistido."""
    if not _text_ok(rule, "name", name):
        return False
    want_kind = rule.get("kind")
    if want_kind is None:
        return True
    return kind_of(name) == str(want_kind)


def entity_rule_matches_text(rule: Mapping[str, Any], name: str) -> bool:
    """Só o trio textual (kind conferido à parte — v. scorer)."""
    return _text_ok(rule, "name", name)


def edge_rule_matches(rule: Mapping[str, Any], edge: Mapping[str, Any]) -> bool:
    for f in _EDGE_TEXT_FIELDS:
        if not _text_ok(rule, f, str(edge.get(f, ""))):
            return False
    return all(_choice_ok(rule, f, str(edge.get(f, ""))) for f in _EDGE_CHOICE_FIELDS)


def diagnostic_rule_matches(rule: Mapping[str, Any], diag: Mapping[str, Any]) -> bool:
    for f in _DIAG_TEXT_FIELDS:
        if not _text_ok(rule, f, str(diag.get(f, ""))):
            return False
    return _choice_ok(rule, "severity", str(diag.get("severity", "")))


def _rule_desc(rule: Mapping[str, Any]) -> str:
    """Renderização legível de uma regra para listas de diff."""
    parts = {k: v for k, v in rule.items() if k not in {"note", "count", "max_count", "min_count"}}
    note = rule.get("note")
    body = " ".join(f"{k}={v!r}" for k, v in sorted(parts.items()))
    return f"{body}  # {note}" if note else body


def _prf(tp: int, fp: int, fn: int) -> dict[str, float | None]:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f1 = 2 * p * r / (p + r) if p is not None and r is not None and p + r > 0 else None
    return {
        "precision": round(p, 4) if p is not None else None,
        "recall": round(r, 4) if r is not None else None,
        "f1": round(f1, 4) if f1 is not None else None,
    }


@dataclass
class _ClassScore:
    """TP/FP/FN de uma classe (entities ou relations)."""

    complete: bool = False
    extracted: int = 0
    labeled_rules: int = 0
    tp: int = 0
    fp: int = 0
    fn: int = 0
    unaudited: int = 0
    missing: list[str] = field(default_factory=list)
    fabricated: list[dict[str, str]] = field(default_factory=list)
    unexpected: list[str] = field(default_factory=list)
    kind_mismatches: list[str] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)

    def metrics(self) -> dict[str, float | None]:
        return _prf(self.tp, self.fp, self.fn)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "complete": self.complete,
            "extracted": self.extracted,
            "labeled_rules": self.labeled_rules,
            "tp": self.tp,
            "fp": self.fp,
            "fn": self.fn,
            "unaudited": self.unaudited,
            **self.metrics(),
        }
        for key in (
            "missing",
            "fabricated",
            "unexpected",
            "kind_mismatches",
            "conflicts",
        ):
            vals = getattr(self, key)
            if vals:
                out[key] = vals
        return out


def _score_class(
    items: Sequence[str],
    spec: Mapping[str, Any],
    match_text: Callable[[Mapping[str, Any], str], bool],
    kind_bad: set[int] | None = None,
) -> _ClassScore:
    """Núcleo do diff: ``items`` são as chaves renderizadas extraídas.

    ``match_text`` decide se um item casa uma regra (trio textual +
    campos de escolha, SEM ``kind`` — mismatches de kind são
    contabilizados à parte via ``kind_bad``).
    """
    score = _ClassScore(
        complete=bool(spec.get("complete", False)),
        extracted=len(items),
    )
    expected = [r for r in spec.get("expected", ()) if isinstance(r, Mapping)]
    forbidden = [r for r in spec.get("forbidden", ()) if isinstance(r, Mapping)]
    score.labeled_rules = len(expected) + len(forbidden)

    covered: set[int] = set()
    over: set[int] = set()
    bad: set[int] = set()
    hits_of: dict[int, list[int]] = {}

    for rule in expected:
        hits = sorted(i for i, it in enumerate(items) if match_text(rule, it))
        want = rule.get("count")
        if want is None:
            if hits:
                covered.update(hits)
            else:
                score.fn += 1
                score.missing.append(_rule_desc(rule))
            continue
        k = int(want)
        if len(hits) < k:
            score.fn += k - len(hits)
            score.missing.append(f"{_rule_desc(rule)}  # count: expected {k}, got {len(hits)}")
        covered.update(hits[:k])
        over.update(hits[k:])
        hits_of[id(rule)] = hits

    for rule in forbidden:
        hits = sorted(i for i, it in enumerate(items) if match_text(rule, it))
        maxc = rule.get("max_count")
        surplus = hits if maxc is None else hits[int(maxc) :]
        for i in surplus:
            bad.add(i)
            score.fabricated.append({"item": items[i], "rule": _rule_desc(rule)})

    if score.complete:
        for i, it in enumerate(items):
            if i not in covered and i not in over and i not in bad:
                score.unexpected.append(it)
                bad.add(i)

    kind_bad = kind_bad or set()
    fp_items = bad | over
    score.conflicts = sorted(items[i] for i in covered & fp_items)
    score.tp = len(covered - fp_items - kind_bad)
    score.fp = len(fp_items)
    score.unaudited = len(items) - len(covered | fp_items)
    return score


def _kind_mismatch_idx(
    expected: Sequence[Any],
    entity_names: Sequence[str],
    kind_of: Callable[[str], str],
) -> set[int]:
    """Índices de entidades cujo kind persistido difere do rotulado.

    Match textual ok mas kind difere — o item existe (não é fabricação)
    mas o fato "X com kind K" não foi extraído: sai do TP e vira linha
    própria do diff (``kind_mismatches``).
    """
    bad: set[int] = set()
    for rule in expected:
        if not isinstance(rule, Mapping) or "kind" not in rule:
            continue
        for i, name in enumerate(entity_names):
            if entity_rule_matches_text(rule, name) and kind_of(name) != str(rule["kind"]):
                bad.add(i)
    return bad


def _pattern_violations(
    patterns: Sequence[Any],
    scan_texts: Sequence[tuple[str, str]],
) -> list[dict[str, str]]:
    """Ocorrências de substrings proibidos no corpus varrido."""
    hits: list[dict[str, str]] = []
    for pat in patterns:
        if not isinstance(pat, Mapping):
            continue
        needle = str(pat.get("substr", ""))
        if not needle:
            continue
        for loc, text in scan_texts:
            if needle in text:
                hits.append(
                    {
                        "substr": needle,
                        "location": loc,
                        "note": str(pat.get("note", "")),
                    }
                )
    return hits


def score_domain(
    truth: Mapping[str, Any],
    report: Mapping[str, Any],
    *,
    kind_of: Callable[[str], str] | None = None,
    scan_texts: Sequence[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Compara um truth set com o relatório persistido do domínio.

    ``report`` é o retorno de ``run_domain_eval`` (entidades como
    display names, arestas renderizadas, diagnósticos). ``kind_of``
    resolve display→kind persistido (para regras com ``kind`` e para
    ``kind_mismatches``). ``scan_texts`` são pares ``(local, texto)``
    varridos por ``patterns_forbidden``.
    """
    kind_of = kind_of or (lambda _name: "")
    entity_names = [str(n) for n in (report.get("entities", {}) or {}).get("names", ())]
    edges = [e for e in report.get("edges", ()) if isinstance(e, Mapping)]
    diags = [d for d in report.get("diagnostics", ()) if isinstance(d, Mapping)]
    edge_keys = [
        f"{e.get('type')}|{e.get('from')}|{e.get('to')}|"
        f"{e.get('status')}|{e.get('confidence')}|{e.get('resolver')}"
        for e in edges
    ]

    e_spec = truth.get("entities", {}) or {}
    kind_bad_idx = _kind_mismatch_idx(e_spec.get("expected", ()), entity_names, kind_of)
    ent = _score_class(
        entity_names,
        e_spec,
        entity_rule_matches_text,
        kind_bad=kind_bad_idx,
    )
    # o fato "X com kind K" não existe → cada mismatch é um FN do rótulo
    ent.fn += len(kind_bad_idx)
    for i in sorted(kind_bad_idx):
        ent.kind_mismatches.append(
            f"{entity_names[i]}  # kind persistido: {kind_of(entity_names[i])}"
        )

    rel = _score_edges(edge_keys, edges, truth.get("relations", {}) or {})

    diag_score = _score_diagnostics(diags, truth.get("diagnostics_expected", ()))

    pattern_hits = _pattern_violations(truth.get("patterns_forbidden", ()), scan_texts)

    totals = {
        "tp": ent.tp + rel.tp + diag_score["tp"],
        "fp": ent.fp + rel.fp + len(pattern_hits),
        "fn": ent.fn + rel.fn + diag_score["fn"],
    }
    overall = {**totals, **_prf(totals["tp"], totals["fp"], totals["fn"])}

    return {
        "domain": truth.get("domain", report.get("domain", "")),
        "fixture": truth.get("fixture", report.get("fixture", "")),
        "index_status": report.get("index_status", ""),
        "entities": ent.to_dict(),
        "relations": rel.to_dict(),
        "diagnostics": diag_score,
        "patterns_forbidden": {
            "rules": len(truth.get("patterns_forbidden", ())),
            "violations": pattern_hits,
        },
        "overall": overall,
    }


def _score_edges(
    edge_keys: Sequence[str],
    edges: Sequence[Mapping[str, Any]],
    spec: Mapping[str, Any],
) -> _ClassScore:
    """Mesma contabilização de ``_score_class`` mas casando dicts."""
    score = _ClassScore(
        complete=bool(spec.get("complete", False)),
        extracted=len(edges),
    )
    expected = [r for r in spec.get("expected", ()) if isinstance(r, Mapping)]
    forbidden = [r for r in spec.get("forbidden", ()) if isinstance(r, Mapping)]
    score.labeled_rules = len(expected) + len(forbidden)

    covered: set[int] = set()
    over: set[int] = set()
    bad: set[int] = set()

    for rule in expected:
        hits = sorted(i for i, e in enumerate(edges) if edge_rule_matches(rule, e))
        want = rule.get("count")
        if want is None:
            if hits:
                covered.update(hits)
            else:
                score.fn += 1
                score.missing.append(_rule_desc(rule))
            continue
        k = int(want)
        if len(hits) < k:
            score.fn += k - len(hits)
            score.missing.append(f"{_rule_desc(rule)}  # count: expected {k}, got {len(hits)}")
        covered.update(hits[:k])
        over.update(hits[k:])

    for rule in forbidden:
        hits = sorted(i for i, e in enumerate(edges) if edge_rule_matches(rule, e))
        maxc = rule.get("max_count")
        surplus = hits if maxc is None else hits[int(maxc) :]
        for i in surplus:
            bad.add(i)
            score.fabricated.append({"item": edge_keys[i], "rule": _rule_desc(rule)})

    if score.complete:
        for i, key in enumerate(edge_keys):
            if i not in covered and i not in over and i not in bad:
                score.unexpected.append(key)
                bad.add(i)

    fp_items = bad | over
    score.conflicts = sorted(edge_keys[i] for i in covered & fp_items)
    score.tp = len(covered - fp_items)
    score.fp = len(fp_items)
    score.unaudited = len(edges) - len(covered | fp_items)
    return score


def _score_diagnostics(
    diags: Sequence[Mapping[str, Any]],
    expected: Sequence[Any],
) -> dict[str, Any]:
    rules = [r for r in expected if isinstance(r, Mapping)]
    tp = fn = 0
    missing: list[str] = []
    for rule in rules:
        need = int(rule.get("min_count", 1))
        hits = sum(1 for d in diags if diagnostic_rule_matches(rule, d))
        if hits >= need:
            tp += 1
        else:
            fn += 1
            missing.append(f"{_rule_desc(rule)}  # min_count: expected {need}, got {hits}")
    return {
        "labeled_rules": len(rules),
        "extracted": len(diags),
        "tp": tp,
        "fp": 0,
        "fn": fn,
        "missing": missing,
        **_prf(tp, 0, fn),
    }


def load_truth(truth_dir: Path, domain: str) -> dict[str, Any] | None:
    path = Path(truth_dir) / f"{domain}.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------
# pipeline real + extras persistidos (kind, corpus de padrões)
# ---------------------------------------------------------------------


def _display(identity_key_str: str) -> str:
    """``identity_key`` canônica → display ``dom:cat.ns.name@env``."""
    from code_atlas.domains.identity import DomainIdentity

    segs = DomainIdentity.parse_key(identity_key_str)
    if segs is None or len(segs) < 7:
        return identity_key_str
    domain, repository, root, catalog, namespace, env, qname = segs[:7]
    return DomainIdentity(
        domain=domain,
        repository=repository,
        root=root,
        catalog=catalog,
        namespace=namespace,
        environment=env,
        qualified_name=qname,
    ).display()


def read_snapshot_extras(
    db_path: Path, snapshot_id: int
) -> tuple[dict[str, str], list[tuple[str, str]]]:
    """Kind por entidade + corpus textual para ``patterns_forbidden``.

    Reabre o ``atlas.db`` do cenário (persistido por ``run_domain_eval``)
    e lê: ``kind`` de ``domain_entities`` (checagem de ``kind`` nas
    regras) e os textos varridos por padrões proibidos — identity_key,
    qualified_name, name e atributos JSON de entidades e ocorrências,
    ``to_unresolved`` de arestas.
    """
    from code_atlas.storage import connect

    kind_of: dict[str, str] = {}
    corpus: list[tuple[str, str]] = []
    conn = connect(db_path)
    try:
        for row in conn.execute(
            "SELECT identity_key, kind, qualified_name, name, attributes FROM domain_entities"
        ):
            disp = _display(str(row["identity_key"]))
            kind_of[disp] = str(row["kind"])
            corpus.append((f"entity:{disp}", str(row["identity_key"])))
            corpus.append((f"entity-qname:{disp}", str(row["qualified_name"])))
            corpus.append((f"entity-name:{disp}", str(row["name"])))
            corpus.append((f"entity-attrs:{disp}", str(row["attributes"])))
        for row in conn.execute(
            "SELECT deo.attributes, de.identity_key"
            " FROM domain_entity_occurrences deo"
            " JOIN domain_entities de ON de.id = deo.entity_id"
            " WHERE deo.snapshot_id = ?",
            (snapshot_id,),
        ):
            corpus.append(
                (
                    "occurrence-attrs:" + _display(str(row["identity_key"])),
                    str(row["attributes"]),
                )
            )
        for row in conn.execute(
            "SELECT type, to_unresolved FROM domain_relations"
            " WHERE snapshot_id = ? AND to_unresolved IS NOT NULL",
            (snapshot_id,),
        ):
            corpus.append(
                (
                    f"relation-unresolved:{row['type']}",
                    str(row["to_unresolved"]),
                )
            )
    finally:
        conn.close()
    return kind_of, corpus


def _snapshot_id(db_path: Path) -> int | None:
    from code_atlas.storage import connect

    conn = connect(db_path)
    try:
        row = conn.execute("SELECT MAX(snapshot_id) s FROM domain_entity_occurrences").fetchone()
        if row is not None and row["s"] is not None:
            return int(row["s"])
        row = conn.execute("SELECT MAX(id) s FROM snapshots").fetchone()
        return int(row["s"]) if row is not None and row["s"] is not None else None
    finally:
        conn.close()


def run_truth_eval(
    repo_root: Path,
    eval_id: str,
    work_dir: Path,
    truth: Mapping[str, Any],
) -> dict[str, Any]:
    """Pipeline real (``run_domain_eval``) + diff contra o truth set.

    O snapshot é isolado: um ``<eval_id>.db`` por domínio dentro de
    ``work_dir`` — o mesmo isolamento do harness golden. Retorna o
    bloco de score de ``score_domain`` enriquecido com os totais
    extraídos (transparência do universo medido).
    """
    from code_atlas.eval.domain_eval import run_domain_eval, spec_by_id

    spec = spec_by_id(eval_id)
    report: dict[str, Any] = run_domain_eval(Path(repo_root), spec, Path(work_dir))
    if report.get("index_status") != "success":
        return {
            "domain": eval_id,
            "index_status": report.get("index_status"),
            "error": report.get("error"),
        }

    db_path = Path(work_dir) / f"{eval_id}.db"
    kind_of: dict[str, str] = {}
    corpus: list[tuple[str, str]] = []
    snapshot_id = _snapshot_id(db_path) if db_path.is_file() else None
    if snapshot_id is not None:
        kind_of, corpus = read_snapshot_extras(db_path, snapshot_id)
    for e in report.get("edges", ()):
        corpus.append((f"edge:{e['type']}", f"{e['from']} {e['to']}"))
    for d in report.get("diagnostics", ()):
        corpus.append((f"diagnostic:{d['code']}", f"{d['severity']} {d['path']}"))

    scored = score_domain(
        truth,
        report,
        kind_of=lambda n: kind_of.get(n, ""),
        scan_texts=corpus,
    )
    entities = report.get("entities", {})
    relations = report.get("relations", {})
    scored["extracted_totals"] = {
        "entities": entities.get("total"),
        "relations": relations.get("total"),
        "diagnostics": len(report.get("diagnostics", ())),
        "edges_skipped": relations.get("edges_skipped"),
    }
    return scored


_ENT_FIELDS = {"name", "name_prefix", "name_contains", "kind", "count", "max_count", "note"}
_REL_FIELDS = {
    "type",
    "from",
    "to",
    "status",
    "confidence",
    "resolver",
    "type_prefix",
    "from_prefix",
    "to_prefix",
    "resolver_prefix",
    "type_contains",
    "from_contains",
    "to_contains",
    "resolver_contains",
    "count",
    "max_count",
    "note",
}
_DIAG_FIELDS = {
    "code",
    "code_prefix",
    "code_contains",
    "path",
    "path_prefix",
    "path_contains",
    "severity",
    "min_count",
    "note",
}


def _validate_class_rules(truth: Mapping[str, Any], cls: str, fields: set[str]) -> list[str]:
    """Valida ``expected``/``forbidden`` de uma classe (entities/relations)."""
    errs: list[str] = []
    spec = truth.get(cls, {}) or {}
    if not isinstance(spec, Mapping):
        return [f"{cls}: not a mapping"]
    for group in ("expected", "forbidden"):
        for i, rule in enumerate(spec.get(group, ())):
            if not isinstance(rule, Mapping):
                errs.append(f"{cls}.{group}[{i}]: not a mapping")
                continue
            extra = set(rule) - fields
            if extra:
                errs.append(f"{cls}.{group}[{i}]: unknown {sorted(extra)}")
            if group == "expected" and not any(
                k in rule for k in fields if not k.endswith(("count", "note"))
            ):
                errs.append(f"{cls}.{group}[{i}]: rule has no criteria")
    return errs


def validate_truth(truth: Mapping[str, Any]) -> list[str]:
    """Erros estruturais do truth set — schema, campos e tipos."""
    errs: list[str] = []
    if int(truth.get("schema", -1)) != TRUTH_SCHEMA:
        errs.append(f"schema: expected {TRUTH_SCHEMA}, got {truth.get('schema')}")
    if not truth.get("fixture"):
        errs.append("missing fixture")
    errs += _validate_class_rules(truth, "entities", _ENT_FIELDS)
    errs += _validate_class_rules(truth, "relations", _REL_FIELDS)
    for i, rule in enumerate(truth.get("diagnostics_expected", ())):
        if not isinstance(rule, Mapping):
            errs.append(f"diagnostics_expected[{i}]: not a mapping")
            continue
        extra = set(rule) - _DIAG_FIELDS
        if extra:
            errs.append(f"diagnostics_expected[{i}]: unknown {sorted(extra)}")
    for i, pat in enumerate(truth.get("patterns_forbidden", ())):
        if not isinstance(pat, Mapping) or not pat.get("substr"):
            errs.append(f"patterns_forbidden[{i}]: missing substr")
    return errs
