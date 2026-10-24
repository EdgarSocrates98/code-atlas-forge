"""Permite ``python -m code_atlas`` como equivalente ao entry point ``code-atlas``."""

from code_atlas.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
