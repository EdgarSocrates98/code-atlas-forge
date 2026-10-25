#!/usr/bin/env python3
"""Gerador de repositório sintético para benchmarks de escala do grafo.

Cycle 6.1 Phase 5 — alimenta ``explorer/e2e/graph-scale.spec.ts``. Emite
um repo Python **determinístico** (sem RNG, forma fechada) com contagem
controlada de arquivos — que é o que ``GET /graph/overview`` agrega como
nós de arquitetura (1 nó por arquivo; todo arquivo ganha ocorrência de
``file``+``module`` no índice, mesmo vazio).

Layout plano na raiz — ``mod_{i:04d}.py`` — para que ``--files N`` produza
**exatamente N nós** na overview e os qualified names saiam na forma
``mod_0001.func_0001`` (sem pacotes inflando a contagem com ``__init__``).

Cada módulo define:

- ``func_{i:04d}(x)`` — função top-level;
- ``K{i:04d}`` — classe com método ``m{i:04d}``;
- um ``import mod_{i+1 mod N}`` (anel) + chamadas a
  ``mod_{i+1}.func_{i+1}`` — relações reais de import/call.

Uso::

    python3 tests/fixtures/gen_large_repo.py --files 3000 --out /tmp/repo-3000
    python3 tests/fixtures/gen_large_repo.py --files 100 --out r --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

MOD_BODY = '''"""mod_{i:04d} — fixture sintético de escala (determinístico)."""
import mod_{n:04d}


class K{i:04d}:
    """Classe sintética — alvo de chamada no anel."""

    def m{i:04d}(self, x):
        return mod_{n:04d}.func_{n:04d}(x)


def func_{i:04d}(x):
    return mod_{n:04d}.func_{n:04d}(x) + {i}
'''


def module_relpath(i: int) -> str:
    return f"mod_{i:04d}.py"


def gen_repo(root: Path, n_files: int) -> dict[str, int]:
    """Emite ``n_files`` módulos flat; cada um chama o próximo em anel.

    Retorna contagens reais escritas (para o relatório do benchmark).
    """
    root.mkdir(parents=True, exist_ok=True)
    for i in range(n_files):
        n = (i + 1) % n_files  # anel: cada módulo chama o próximo
        body = MOD_BODY.format(i=i, n=n)
        (root / module_relpath(i)).write_text(body, encoding="utf-8")
    return {"module_files": n_files}


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Repo sintético determinístico para benchmark de grafo"
    )
    ap.add_argument("--files", type=int, required=True,
                    help="módulos gerados (= nós na overview do grafo)")
    ap.add_argument("--out", type=Path, required=True,
                    help="diretório raiz do repo (criado se ausente)")
    ap.add_argument("--json", action="store_true",
                    help="imprime o manifesto em JSON")
    args = ap.parse_args()

    if args.files < 1:
        print("--files deve ser >= 1", file=sys.stderr)
        return 2

    stats = gen_repo(args.out, args.files)
    manifest = {
        "tool": "gen_large_repo",
        "deterministic": True,
        "files": args.files,
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
