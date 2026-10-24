"""Camada de persistência em memória — determinística."""

_STORE: dict[str, dict[int, dict]] = {"users": {}, "invoices": {}}
_SEQ = {"users": 0, "invoices": 0}


def insert(table: str, row: dict) -> int:
    _SEQ[table] += 1
    rid = _SEQ[table]
    row = dict(row, id=rid)
    _STORE[table][rid] = row
    return rid


def get(table: str, rid: int) -> dict | None:
    return _STORE[table].get(rid)


def where(table: str, **kw) -> list[dict]:
    return [r for r in _STORE[table].values()
            if all(r.get(k) == v for k, v in kw.items())]
