#!/usr/bin/env bash
# scripts/release_check.sh — full release gate battery, non-interactive.
#
# Spec: factory/specs/inbox/lf-0142-cycle8-release-eng.md (cycle-8 Phase 16)
# Docs: docs/RELEASE-CHECKLIST.md, docs/RELEASING.md
#
# Runs every gate CI runs plus release-specific ones (version/CHANGELOG
# consistency, wheel inventory, clean-venv install smoke). Prints a
# PASS/FAIL/SKIP table and exits non-zero if any gate FAILs.
# SKIP only via explicit flag or a tool verifiably absent — printed as
# NOT RUN, never silently.
#
# Usage:
#   scripts/release_check.sh                  # full battery (release cut)
#   scripts/release_check.sh --skip-explorer  # iteration only — never for cut
#   scripts/release_check.sh --skip-e2e       # iteration only
#   scripts/release_check.sh --skip-audits    # iteration only

set -u -o pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

SKIP_EXPLORER=0
SKIP_E2E=0
SKIP_AUDITS=0
for arg in "$@"; do
    case "$arg" in
        --skip-explorer) SKIP_EXPLORER=1 ;;
        --skip-e2e) SKIP_E2E=1 ;;
        --skip-audits) SKIP_AUDITS=1 ;;
        -h|--help)
            sed -n '2,22p' "$0"; exit 0 ;;
        *)
            echo "unknown flag: $arg (see --help)" >&2; exit 2 ;;
    esac
done

# --- tool resolution: prefer repo venv, else PATH -------------------------
if [ -x "$ROOT/.venv/bin/python" ]; then PY="$ROOT/.venv/bin/python"; else PY="$(command -v python3 || true)"; fi
[ -n "$PY" ] || { echo "FATAL: python3/.venv not found" >&2; exit 2; }
VENV_BIN="$(dirname "$PY")"
tool() {  # tool NAME — resolve from venv bin first, then PATH
    if [ -x "$VENV_BIN/$1" ]; then echo "$VENV_BIN/$1"; else command -v "$1" 2>/dev/null || true; fi
}

# --- gate bookkeeping ------------------------------------------------------
declare -a GATE_NAMES=()
declare -a GATE_STATUS=()
LOGDIR="$(mktemp -d /tmp/atlas-release-check.XXXXXX)"
trap 'rm -rf "$LOGDIR"' EXIT

record() {  # record NAME PASS|FAIL|SKIP
    GATE_NAMES+=("$1"); GATE_STATUS+=("$2")
    case "$2" in
        PASS) echo "  PASS  $1" ;;
        SKIP) echo "  SKIP  $1  (NOT RUN)" ;;
        FAIL) echo "  FAIL  $1  (log: $LOGDIR/$1.log)" ;;
    esac
}

gate() {  # gate NAME cmd... — run, record, show log tail on failure
    local name="$1"; shift
    echo "== gate: $name"
    if "$@" >"$LOGDIR/$name.log" 2>&1; then
        record "$name" PASS
    else
        record "$name" FAIL
        tail -n 25 "$LOGDIR/$name.log" | sed 's/^/       /'
    fi
}

have() { [ -n "$(tool "$1")" ]; }

echo "release_check.sh — root: $ROOT"
echo "python: $PY ($("$PY" --version 2>&1))"
echo "logs:   $LOGDIR"
echo

# --- gate 1: version consistency -------------------------------------------
VER="$("$PY" - <<'EOF'
import re, pathlib
init = pathlib.Path("src/code_atlas/__init__.py").read_text()
m = re.search(r'__version__\s*=\s*"([^"]+)"', init)
print(m.group(1) if m else "")
EOF
)"
echo "version: ${VER:-<unresolved>}"
if [ -z "$VER" ]; then
    record "version-source" FAIL
elif grep -q "^## \[${VER}\]" CHANGELOG.md; then
    record "version-changelog-consistency" PASS
else
    record "version-changelog-consistency" FAIL
    echo "       CHANGELOG.md has no '## [${VER}]' section" | sed 's/^/       /'
fi

# --- gates 2-4: lint / types / tests ---------------------------------------
if have ruff;  then gate "ruff"  "$(tool ruff)" check src tests;  else record "ruff"  SKIP; fi
if have mypy;  then gate "mypy"  "$(tool mypy)" src;              else record "mypy"  SKIP; fi
if have pytest; then gate "pytest" "$(tool pytest)" -q;           else record "pytest" SKIP; fi

# --- gate 5: build wheel + sdist --------------------------------------------
WHEEL="dist/code_atlas_forge-${VER}-py3-none-any.whl"
SDIST="dist/code_atlas_forge-${VER}.tar.gz"
rm -f "$WHEEL" "$SDIST"
gate "build-wheel-sdist" "$PY" -m build

# --- gate 6: wheel inventory -------------------------------------------------
echo "== gate: wheel-inventory"
if [ ! -f "$WHEEL" ]; then
    record "wheel-inventory" FAIL
    echo "       expected wheel missing: $WHEEL" | sed 's/^/       /'
else
    "$PY" - "$WHEEL" >"$LOGDIR/wheel-inventory.log" 2>&1 <<'EOF'
import sys, zipfile
wheel = sys.argv[1]
need = [
    "code_atlas/api/", "code_atlas/context/", "code_atlas/contracts/",
    "code_atlas/discovery/", "code_atlas/domains/", "code_atlas/eval/",
    "code_atlas/federation/", "code_atlas/graph/", "code_atlas/impact/",
    "code_atlas/indexing/", "code_atlas/observability/",
    "code_atlas/parsers/", "code_atlas/resolvers/", "code_atlas/search/",
    "code_atlas/storage/",
]
names = zipfile.ZipFile(wheel).namelist()
missing = [p for p in need if not any(n.startswith(p) for n in names)]
print(f"{wheel}: {len(names)} entries, {len(need)} required package dirs")
for p in need:
    n = sum(1 for x in names if x.startswith(p) and x.endswith('.py'))
    print(f"  {'ok' if p not in missing else 'MISSING':7} {p} ({n} .py)")
sys.exit(1 if missing else 0)
EOF
    if [ $? -eq 0 ]; then record "wheel-inventory" PASS; else record "wheel-inventory" FAIL; cat "$LOGDIR/wheel-inventory.log" | sed 's/^/       /'; fi
fi

# --- gate 7: clean-venv install smoke ----------------------------------------
echo "== gate: clean-install-smoke"
if [ ! -f "$WHEEL" ]; then
    record "clean-install-smoke" SKIP
else
    TMPVENV="$LOGDIR/smoke-venv"
    {
        "$PY" -m venv "$TMPVENV" &&
        "$TMPVENV/bin/pip" install --quiet --no-deps "$WHEEL" &&
        out="$("$TMPVENV/bin/code-atlas" --version)" &&
        echo "$out" &&
        [ "$out" = "code-atlas $VER" ]
    } >"$LOGDIR/clean-install-smoke.log" 2>&1
    if [ $? -eq 0 ]; then record "clean-install-smoke" PASS; else record "clean-install-smoke" FAIL; tail -n 15 "$LOGDIR/clean-install-smoke.log" | sed 's/^/       /'; fi
fi

# --- gate 8: functional CLI smoke (dev venv, isolated data dir) ---------------
echo "== gate: cli-smoke"
SMOKE_DATA="$(mktemp -d)"; SMOKE_CONF="$(mktemp -d)"
{
    export CODE_ATLAS_DATA_DIR="$SMOKE_DATA" CODE_ATLAS_CONFIG_DIR="$SMOKE_CONF"
    ATLAS="$(tool code-atlas)"
    [ -n "$ATLAS" ] || exit 3
    "$ATLAS" --version &&
    "$ATLAS" doctor >/dev/null &&
    "$ATLAS" index tests/fixtures/sample_repo --json >/dev/null &&
    "$ATLAS" status --json >/dev/null
} >"$LOGDIR/cli-smoke.log" 2>&1
if [ $? -eq 0 ]; then record "cli-smoke" PASS; else record "cli-smoke" FAIL; tail -n 15 "$LOGDIR/cli-smoke.log" | sed 's/^/       /'; fi
rm -rf "$SMOKE_DATA" "$SMOKE_CONF"

# --- gates 9-13: explorer ----------------------------------------------------
if [ "$SKIP_EXPLORER" -eq 1 ]; then
    for g in npm-ci explorer-build explorer-unit explorer-e2e npm-audit; do record "$g" SKIP; done
else
    if have npm; then
        # `npm --prefix explorer …` roda scripts com cwd=explorer/ —
        # mantém o bookkeeping de gates no shell principal (sem subshell).
        gate "npm-ci" npm --prefix explorer ci
        gate "explorer-build" npm --prefix explorer run build
        gate "explorer-unit" npm --prefix explorer test -- --watch=false --browsers=ChromeHeadless
        if [ "$SKIP_E2E" -eq 1 ]; then
            record "explorer-e2e" SKIP
        else
            gate "explorer-e2e" npm --prefix explorer run e2e
        fi
        if [ "$SKIP_AUDITS" -eq 1 ]; then
            record "npm-audit" SKIP
        else
            gate "npm-audit" npm --prefix explorer audit --omit=dev --audit-level=critical
        fi
    else
        for g in npm-ci explorer-build explorer-unit explorer-e2e npm-audit; do record "$g" SKIP; done
    fi
fi

# --- gate 14: pip-audit (same recipe as CI) ------------------------------------
if [ "$SKIP_AUDITS" -eq 1 ]; then
    record "pip-audit" SKIP
elif have pip-audit; then
    echo "== gate: pip-audit"
    REQS="$LOGDIR/reqs.txt"
    "$(tool pip)" freeze 2>/dev/null | grep -v "code-atlas-forge" > "$REQS"
    if "$(tool pip-audit)" -r "$REQS" --strict >"$LOGDIR/pip-audit.log" 2>&1; then
        record "pip-audit" PASS
    else
        record "pip-audit" FAIL
        tail -n 25 "$LOGDIR/pip-audit.log" | sed 's/^/       /'
    fi
else
    record "pip-audit" SKIP
fi

# --- summary -------------------------------------------------------------------
echo
echo "================ RELEASE GATE SUMMARY ================"
fails=0; skips=0
for i in "${!GATE_NAMES[@]}"; do
    printf "  %-36s %s\n" "${GATE_NAMES[$i]}" "${GATE_STATUS[$i]}"
    [ "${GATE_STATUS[$i]}" = "FAIL" ] && fails=$((fails+1))
    [ "${GATE_STATUS[$i]}" = "SKIP" ] && skips=$((skips+1))
done
echo "-----------------------------------------------------"
echo "  version: $VER    FAIL: $fails    SKIP(NOT RUN): $skips"
if [ "$fails" -gt 0 ]; then
    echo "  RESULT: FAIL — release gate did not pass"
    exit 1
fi
if [ "$skips" -gt 0 ]; then
    echo "  RESULT: PASS WITH NOT RUN — skipped gates are listed above;"
    echo "          a release cut requires a full-battery run (no flags)."
fi
echo "  RESULT: PASS"
exit 0
