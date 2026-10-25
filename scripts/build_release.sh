#!/usr/bin/env bash
# scripts/build_release.sh — build reproduzível do release (wheel + sdist).
#
# Spec: factory/specs/inbox/lf-0148-cycle81-wheel-packaging.md
# (cycle-8.1, Phase 3+4). Docs: docs/RELEASING.md §"Empacotamento".
#
# Pipeline:
#   1. npm ci + npm run build       (explorer/dist/explorer/browser)
#   2. copia a dist compilada para   src/code_atlas/explorer_dist/
#      (dados de pacote, gitignored — entra no wheel/sdist via
#      [tool.setuptools.package-data])
#   3. SOURCE_DATE_EPOCH fixado + python -m build
#
# Reprodutibilidade (Phase 4): SOURCE_DATE_EPOCH normaliza timestamps
# dos zips/tars do setuptools. Resolução, em ordem:
#   $SOURCE_DATE_EPOCH do ambiente  →  timestamp do HEAD (`git log -1
#   --format=%ct`). Dois builds no mesmo commit produzem artefatos
#   byte-idênticos (verificado em docs/RELEASING.md).
#
# Sem assets gerados, `python -m build` produz wheel/sdist API-only —
# comportamento intencional e documentado (não é erro).
#
# Usage:
#   scripts/build_release.sh              # explorer + wheel + sdist
#   scripts/build_release.sh --api-only   # pula npm/cópia (usa o que
#                                         # já estiver em explorer_dist)

set -eu -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

API_ONLY=0
for arg in "$@"; do
    case "$arg" in
        --api-only) API_ONLY=1 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) echo "unknown flag: $arg (see --help)" >&2; exit 2 ;;
    esac
done

# POSIX venvs keep tools under bin/, Windows venvs under Scripts/ with a
# .exe suffix; bare Windows PATH usually has `python`/`py`, not `python3`
# (Git Bash homologation — same resolution order as release_check.sh).
PY=""
for cand in "$ROOT/.venv/bin/python" "$ROOT/.venv/bin/python3" \
            "$ROOT/.venv/Scripts/python.exe" "$ROOT/.venv/Scripts/python"; do
    if [ -x "$cand" ]; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then
    for cmd in python3 python py; do
        if command -v "$cmd" >/dev/null 2>&1; then PY="$(command -v "$cmd")"; break; fi
    done
fi
[ -n "$PY" ] || { echo "FATAL: python3/.venv not found" >&2; exit 2; }

PKG_DIST="$ROOT/src/code_atlas/explorer_dist"
ANGULAR_DIST="$ROOT/explorer/dist/explorer/browser"

if [ "$API_ONLY" -eq 0 ]; then
    command -v npm >/dev/null || { echo "FATAL: npm not found — aborting (use --api-only to skip explorer)" >&2; exit 2; }
    echo "== explorer: npm ci"
    npm --prefix explorer ci
    echo "== explorer: npm run build"
    npm --prefix explorer run build
    [ -f "$ANGULAR_DIST/index.html" ] || {
        echo "FATAL: build saiu sem $ANGULAR_DIST/index.html" >&2; exit 1; }
    echo "== staging: $ANGULAR_DIST -> $PKG_DIST"
    rm -rf "$PKG_DIST"
    mkdir -p "$PKG_DIST"
    # cp -Rp ≡ cp -a, but POSIX — BSD/macOS cp has no -a.
    cp -Rp "$ANGULAR_DIST/." "$PKG_DIST/"
else
    echo "== explorer: --api-only (skip npm; explorer_dist usado como está)"
fi

# SOURCE_DATE_EPOCH: ambiente > HEAD commit — fixo por commit ⇒ builds
# byte-idênticos. Sem git e sem env: aborta (release sem epoch não é
# reproduzível — melhor falhar que emitir artefato não-verificável).
if [ -z "${SOURCE_DATE_EPOCH:-}" ]; then
    SOURCE_DATE_EPOCH="$(git -C "$ROOT" log -1 --format=%ct 2>/dev/null || true)"
fi
[ -n "${SOURCE_DATE_EPOCH:-}" ] || {
    echo "FATAL: SOURCE_DATE_EPOCH indeterminado (sem env, sem git)" >&2; exit 2; }
export SOURCE_DATE_EPOCH
echo "== SOURCE_DATE_EPOCH=$SOURCE_DATE_EPOCH ($(date -u -d "@$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date -u -r "$SOURCE_DATE_EPOCH" +%Y-%m-%dT%H:%M:%SZ))"

# Artefatos do release ficam em dist/<name>-<ver>; limpa os da versão
# atual para o inventário não misturar cortes antigos.
VER="$("$PY" - <<'EOF'
import re, pathlib
m = re.search(r'__version__\s*=\s*"([^"]+)"',
              pathlib.Path("src/code_atlas/__init__.py").read_text())
print(m.group(1) if m else "")
EOF
)"
[ -n "$VER" ] || { echo "FATAL: __version__ indeterminado" >&2; exit 1; }
rm -f "dist/code_atlas_forge-${VER}-py3-none-any.whl" "dist/code_atlas_forge-${VER}.tar.gz"

echo "== python -m build (sdist → wheel a partir do sdist)"
"$PY" -m build

# sdist: setuptools honra SOURCE_DATE_EPOCH nos zips do wheel (prova:
# builds repetidos são byte-idênticos), mas grava entradas de diretório
# e metadados gerados (PKG-INFO, egg-info) com o relógio real, e o gzip
# header leva o mtime da compressão. Normaliza o tar: todo membro e o
# header gzip passam a usar o epoch — sdist byte-idêntico por commit.
SDIST_PATH="dist/code_atlas_forge-${VER}.tar.gz"
if [ -f "$SDIST_PATH" ]; then
    echo "== normalize sdist mtimes → SOURCE_DATE_EPOCH"
    "$PY" - "$SDIST_PATH" <<'EOF'
import gzip, io, os, sys, tarfile
epoch = int(os.environ["SOURCE_DATE_EPOCH"])
path = sys.argv[1]
with tarfile.open(path) as tin:
    members = tin.getmembers()
    payload = {m.name: tin.extractfile(m).read() for m in members
               if m.isfile()}
raw = io.BytesIO()
with tarfile.open(fileobj=raw, mode="w|",
                  format=tarfile.PAX_FORMAT) as tout:
    for m in members:
        m.mtime = epoch
        m.uid = m.gid = 0
        m.uname = m.gname = ""
        m.pax_headers = {}
        tout.addfile(m, io.BytesIO(payload[m.name]) if m.isfile()
                     else None)
tmp = path + ".tmp"
with open(tmp, "wb") as f:
    with gzip.GzipFile(filename="", mode="wb", fileobj=f,
                       mtime=epoch) as gz:
        gz.write(raw.getvalue())
os.replace(tmp, path)
print(f"normalized {path} → mtime={epoch}")
EOF
fi

echo
echo "== artifacts"
# sha256sum is GNU coreutils; macOS ships `shasum -a 256` instead.
if command -v sha256sum >/dev/null 2>&1; then SHA256=sha256sum; else SHA256="shasum -a 256"; fi
for f in "dist/code_atlas_forge-${VER}-py3-none-any.whl" "dist/code_atlas_forge-${VER}.tar.gz"; do
    [ -f "$f" ] && $SHA256 "$f"
done
"$PY" - "$VER" <<'EOF'
import sys, zipfile
wheel = f"dist/code_atlas_forge-{sys.argv[1]}-py3-none-any.whl"
try:
    names = zipfile.ZipFile(wheel).namelist()
except FileNotFoundError:
    print(f"wheel ausente: {wheel}"); sys.exit(1)
assets = [n for n in names if n.startswith("code_atlas/explorer_dist/")]
print(f"{wheel}: {len(names)} entries, {len(assets)} explorer assets")
if assets and "code_atlas/explorer_dist/index.html" not in names:
    print("WARN: explorer_dist presente sem index.html"); sys.exit(1)
if not assets:
    print("NOTA: wheel API-only (explorer_dist ausente no build)")
EOF
