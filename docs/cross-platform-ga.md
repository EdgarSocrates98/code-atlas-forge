# Cross-platform — GA homologation (cycle-8.1, lf-0149)

Detail doc: `docs/cycle-8.1-platform-homologation.md` (verbatim
evidence, matrices, NOT RUN register). Commit: `0c0b980`.

## Linux (Ubuntu 24.04, Ryzen 7 5700U, Python 3.12.3, Node 22.23.2)

- `pytest tests/ -q`: **1658 passed, 1 skipped**
- `release_check.sh --skip-e2e`: **13 PASS / 0 FAIL / 1 SKIP**
- CLI smoke (real `requests` repo): 121 files / 945 symbols /
  3953 evidence / 0 failures in 1.484s
- API p50: health 1.69ms, search 37.21ms, graph/overview 5.15ms

## Windows

- Last 3 `main` CI runs: **6/6 windows legs green, zero flakes**,
  identical 14-skip set (symlink-privilege, POSIX git modes,
  case-rename, pyright-LSP).
- Git Bash fixes applied: `Scripts/`+`.exe` venv resolution,
  `python3→python→py` fallback, `${TMPDIR:-/tmp}` `mktemp`,
  `sha256sum→shasum` fallback.
- **NOT RUN:** interactive Windows Explorer session (no Windows box);
  branch CI legs verified post-push.

## Browser

See `docs/browser-compatibility.md` — 9/9 on chromium+firefox+webkit.
