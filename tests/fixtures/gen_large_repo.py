#!/usr/bin/env python3
"""Gerador de repositório sintético para benchmarks de escala do grafo.

Cycle 6.1 Phase 5 — alimenta ``explorer/e2e/graph-scale.spec.ts``. Emite
um repo Python **determinístico** (sem RNG, forma fechada) com contagem
controlada de arquivos — que é o que ``GET /graph/overview`` agrega como
nós de arquitetura (1 nó por arquivo; todo arquivo ganha ocorrência de
``file``+``module`` no índice, mesmo vazio).

Cycle 7 Phase 0.3 — densidade de arestas controlável para
``explorer/e2e/graph-dense.spec.ts``:

- ``--calls M`` — cada ``func_{i:04d}`` chama M alvos distintos
  ``mod_{(i+k) mod N}.func_{(i+k) mod N}`` (k = 1..M) e importa cada um
  deles (a resolução sintática de ``mod_j.func_j`` exige o ``import``).
  ``--calls 1`` reproduz **byte a byte** o anel original.
- ``--hubs H --hub-fanout K`` — os primeiros H módulos ganham uma função
  extra ``hub_{h:04d}`` que chama K alvos distintos
  ``mod_{1 + ((h*K + j) mod (N-1))}`` (j = 0..K-1), todos importados no
  topo — um símbolo de fan-out alto para a rede de ego de
  ``GET /graph/neighbors`` (que trunca em ``limit``, hoje 100 no
  cliente / 200 no servidor).

Layout plano na raiz — ``mod_{i:04d}.py`` — para que ``--files N`` produza
**exatamente N nós** na overview e os qualified names saiam na forma
``mod_0001.func_0001`` (sem pacotes inflando a contagem com ``__init__``).

Cada módulo define:

- ``func_{i:04d}(x)`` — função top-level que chama os M alvos do anel;
- ``K{i:04d}`` — classe com método ``m{i:04d}`` que chama o 1º alvo;
- ``hub_{i:04d}(x)`` — apenas nos H primeiros módulos, chamando K alvos;
- um ``import mod_j`` por alvo — relações reais de IMPORTS/CALLS.

Uso::

    python3 tests/fixtures/gen_large_repo.py --files 3000 --out /tmp/repo-3000
    python3 tests/fixtures/gen_large_repo.py --files 500 --calls 2 \
        --hubs 3 --hub-fanout 150 --out /tmp/dense-500 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ``{imports}`` mantém uma linha ``import mod_j`` por alvo — ``--calls 1``
# sem hubs emite exatamente o corpo do gerador original (compatibilidade
# byte a byte com as fixtures já indexadas do graph-scale).
MOD_BODY = '''"""mod_{i:04d} — fixture sintético de escala (determinístico)."""
{imports}


class K{i:04d}:
    """Classe sintética — alvo de chamada no anel."""

    def m{i:04d}(self, x):
        return mod_{t0:04d}.func_{t0:04d}(x)


def func_{i:04d}(x):
    return {chain} + {i}
'''

# Função de fan-out alto: K chamadas a ``mod_j.func_j`` distintos — o
# formato parenthesizado mantém o arquivo válido para qualquer K ≥ 1.
HUB_BODY = '''

def hub_{i:04d}(x):
    return (
        {hub_chain}
    )
'''


def module_relpath(i: int) -> str:
    return f"mod_{i:04d}.py"


def ring_targets(i: int, n_files: int, calls: int) -> list[int]:
    """Alvos do anel denso: os ``calls`` módulos seguintes (mod N)."""
    return [(i + k) % n_files for k in range(1, calls + 1)]


def hub_targets(h: int, n_files: int, fanout: int) -> list[int]:
    """Alvos do hub h: faixa contígua de ``fanout`` módulos em 1..N-1 —
    disjunta entre hubs enquanto ``hubs * fanout <= N - 1``."""
    return [1 + ((h * fanout + j) % (n_files - 1)) for j in range(fanout)]


def module_body(i: int, n_files: int, calls: int,
                hubs: int, hub_fanout: int) -> str:
    targets = ring_targets(i, n_files, calls)
    hub = hub_targets(i, n_files, hub_fanout) if i < hubs else []
    imports = "\n".join(
        f"import mod_{t:04d}" for t in sorted(set(targets) | set(hub))
    )
    chain = " + ".join(
        f"mod_{t:04d}.func_{t:04d}(x)" for t in targets
    )
    body = MOD_BODY.format(
        i=i, imports=imports, t0=targets[0], chain=chain,
    )
    if hub:
        hub_chain = "\n        + ".join(
            f"mod_{t:04d}.func_{t:04d}(x)" for t in hub
        )
        body += HUB_BODY.format(i=i, hub_chain=hub_chain)
    return body


def gen_repo(root: Path, n_files: int, calls: int = 1,
             hubs: int = 0, hub_fanout: int = 0) -> dict[str, int]:
    """Emite ``n_files`` módulos flat; cada ``func`` chama ``calls`` alvos.

    Retorna contagens reais escritas (para o relatório do benchmark).
    """
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        body = module_body(i, n_files, calls, hubs, hub_fanout)
        (root / module_relpath(i)).write_text(body, encoding="utf-8")
    return {"module_files": n_files}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Repo sintético determinístico para benchmark de grafo"
    )
    ap.add_argument("--files", type=int, required=True,
                    help="módulos gerados (= nós na overview do grafo)")
    ap.add_argument("--calls", type=int, default=1,
                    help="chamadas por módulo (fan-out do anel; default 1)")
    ap.add_argument("--hubs", type=int, default=0,
                    help="módulos iniciais com função hub de alto fan-out")
    ap.add_argument("--hub-fanout", type=int, default=0,
                    help="alvos distintos chamados por cada hub")
    ap.add_argument("--out", type=Path, required=True,
                    help="diretório raiz do repo (criado se ausente)")
    ap.add_argument("--json", action="store_true",
                    help="imprime o manifesto em JSON")
    args = ap.parse_args()

    if args.files < 1:
        print("--files deve ser >= 1", file=sys.stderr)
        return 2
    if args.calls < 1:
        print("--calls deve ser >= 1", file=sys.stderr)
        return 2
    if args.hubs < 0 or args.hub_fanout < 0:
        print("--hubs/--hub-fanout devem ser >= 0", file=sys.stderr)
        return 2
    if bool(args.hubs) != bool(args.hub_fanout):
        print("--hubs e --hub-fanout vêm juntos (ambos > 0)", file=sys.stderr)
        return 2
    if args.calls >= args.files:
        print("--calls deve ser < --files", file=sys.stderr)
        return 2
    if args.hubs:
        if args.hubs > args.files:
            print("--hubs deve ser <= --files", file=sys.stderr)
            return 2
        # alvos vivem em 1..N-1 e são disjuntos entre hubs.
        if args.hubs * args.hub_fanout > args.files - 1:
            print("--hubs * --hub-fanout deve ser <= --files - 1",
                  file=sys.stderr)
            return 2
        if args.hub_fanout >= args.files:
            print("--hub-fanout deve ser < --files", file=sys.stderr)
            return 2

    stats = gen_repo(args.out, args.files, args.calls,
                     args.hubs, args.hub_fanout)
    manifest = {
        "tool": "gen_large_repo",
        "deterministic": True,
        "files": args.files,
        "calls": args.calls,
        "hubs": args.hubs,
        "hub_fanout": args.hub_fanout,
        "out": str(args.out.resolve()),
        **stats,
    }
    if args.json:
        print(json.dumps(manifest, indent=2, sort_keys=True))
    else:
        print(f"generated: {args.out} ({args.files} modules)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
