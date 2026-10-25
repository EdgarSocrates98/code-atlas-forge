#!/usr/bin/env bash
# clone_corpus.sh — baixa o corpus real-world (lf-0128) de forma reproduzível.
#
# Clone shallow (--depth 1) dos repos de benchmarks/realworld/manifest.json
# para benchmarks/.corpus/realworld/ (gitignored — nunca commitar corpora).
# O SHA de cada clone é verificado contra o manifest ao final.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
CORPUS_DIR="$REPO_ROOT/benchmarks/.corpus/realworld"
MANIFEST="$HERE/manifest.json"
PYTHON="${PYTHON:-$REPO_ROOT/.venv/bin/python}"

mkdir -p "$CORPUS_DIR"

"$PYTHON" - "$MANIFEST" "$CORPUS_DIR" <<'PY'
import json, subprocess, sys
from pathlib import Path

manifest = json.loads(Path(sys.argv[1]).read_text())
corpus = Path(sys.argv[2])
failures = []
for repo in manifest["repos"]:
    dest = corpus / repo["id"]
    if not (dest / ".git").is_dir():
        print(f"[clone] {repo['id']} <- {repo['url']}", flush=True)
        subprocess.run(
            ["git", "clone", "--depth", "1", "--quiet", repo["url"], str(dest)],
            check=True,
        )
    sha = subprocess.run(
        ["git", "-C", str(dest), "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    expected = repo["sha"]
    status = "PINNED" if sha == expected else f"DRIFT expected={expected}"
    print(f"[sha] {repo['id']}: {sha} {status}", flush=True)
    if sha != expected:
        failures.append(repo["id"])
if failures:
    print("corpus drift detectado — recrie os clones ou atualize o manifest: "
          + ", ".join(failures), file=sys.stderr)
    sys.exit(1)
print("corpus pronto:", corpus)
PY
