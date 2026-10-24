#!/usr/bin/env python3
"""Benchmark do scanner: corpora sintéticos determinísticos (seeded).

Mede scan completo e re-scan (no-op não é medido aqui — o no-op ocorre no
coordinator; aqui medimos o custo de inventário+fingerprint em si).
Uso: ``python benchmarks/bench_scan.py --files 500 --json``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from code_atlas.discovery import ScanOptions, scan  # noqa: E402

EXTS = [".py", ".py", ".py", ".ts", ".js", ".md", ".json", ".txt", ".go", ".rs"]
SEED = 20261024


def synth_corpus(root: Path, n_files: int, seed: int = SEED) -> None:
    """Gera corpus determinístico: mesmos parâmetros → mesma árvore."""
    rng = random.Random(seed)
    for i in range(n_files):
        depth = rng.randint(0, 3)
        d = root
        for j in range(depth):
            d = d / f"pkg{rng.randint(0, 9)}"
        d.mkdir(parents=True, exist_ok=True)
        ext = rng.choice(EXTS)
        size = rng.choice([64, 256, 1024, 4096, 16384])
        content = hashlib.sha256(f"{seed}:{i}".encode()).hexdigest().encode() * (
            size // 64 + 1
        )
        (d / f"file{i}{ext}").write_bytes(content[:size])


def bench(root: Path, n_files: int, repeats: int) -> dict[str, object]:
    corpus = root / "corpus"
    synth_corpus(corpus, n_files)
    options = ScanOptions()

    cold = []
    for _ in range(repeats):
        t0 = time.monotonic()
        res = scan(corpus, options)
        cold.append(time.monotonic() - t0)

    # "warm": segundo scan imediato (cache de FS do SO)
    warm = []
    for _ in range(repeats):
        t0 = time.monotonic()
        scan(corpus, options)
        warm.append(time.monotonic() - t0)

    total_bytes = sum(f.size_bytes for f in res.files)
    return {
        "files_requested": n_files,
        "files_found": len(res.files),
        "total_bytes": total_bytes,
        "cold_scan_s": {"min": min(cold), "median": sorted(cold)[len(cold) // 2]},
        "warm_scan_s": {"min": min(warm), "median": sorted(warm)[len(warm) // 2]},
        "files_per_s_cold_median": (
            len(res.files) / sorted(cold)[len(cold) // 2] if cold[-1] else 0
        ),
        "seed": SEED,
        "warnings": len(res.warnings),
    }


def main() -> int:
    p = argparse.ArgumentParser(description="Benchmark do scanner Code Atlas")
    p.add_argument("--files", type=int, default=500)
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--json", action="store_true")
    p.add_argument("--keep", action="store_true", help="não remove o corpus")
    args = p.parse_args()

    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="atlas-bench-"))
    try:
        result = bench(tmp, args.files, args.repeats)
        result["platform"] = sys.platform
        result["python"] = sys.version.split()[0]
        if args.json:
            print(json.dumps(result))
        else:
            for k, v in result.items():
                print(f"{k}: {v}")
    finally:
        if not args.keep:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
