"""Consumidor HTTP do workspace cross-domain (lf-0124).

- ``requests.post("/v1/pets")``: método+path literais → o resolver
  cross-domain casa ``POST /v1/pets`` do contrato (resolved).
- ``requests.get("/v1/missing")``: nenhum endpoint nesse path —
  permanece unresolved com diagnóstico, nunca fabricado.
"""

import requests


def post_pet():
    return requests.post("/v1/pets", json={"id": 1})


def get_missing():
    return requests.get("/v1/missing")
