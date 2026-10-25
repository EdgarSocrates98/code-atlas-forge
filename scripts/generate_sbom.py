#!/usr/bin/env python3
"""Generate ``sbom.json`` (CycloneDX 1.5) from the pinned dependency sources.

Stdlib-only by repo policy (dependency-light). Sources of truth:

- ``pyproject.toml`` — ``project.dependencies`` (runtime, exact ``==`` pins)
  and ``project.optional-dependencies`` groups (``dev`` -> excluded scope,
  every other group -> optional scope);
- ``explorer/package-lock.json`` — lockfileVersion 3 ``packages`` map;
  entries flagged ``dev: true`` are dev-only, everything else is prod.

Licenses are emitted when discoverable: npm entries carry ``license`` in
the lockfile; PyPI entries are resolved through ``importlib.metadata``
(License-Expression, then License, then OSI classifier) of the *current*
interpreter — run via ``.venv/bin/python`` so project deps are installed.
A missing license is left absent rather than guessed.

Output is deterministic: stable UUID5 serial, sorted components, no
timestamps — re-running on unchanged inputs is byte-identical.

Usage::

    .venv/bin/python scripts/generate_sbom.py [--check] [--output sbom.json]

``--check`` regenerates in memory and exits non-zero if ``sbom.json`` on
disk differs (freshness gate for CI/release).
"""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import sys
import tomllib
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"
PACKAGE_LOCK = ROOT / "explorer" / "package-lock.json"
DEFAULT_OUTPUT = ROOT / "sbom.json"

SPEC_VERSION = "1.5"
# Deterministic serial: re-generation on identical input must be stable
# (reproducibility policy, lf-0148). UUID5 over a fixed project name.
SERIAL = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "code-atlas-forge/sbom"))

# SPDX-style ids we can emit as CycloneDX ``license.id`` verbatim.
_SPDX_ID = re.compile(r"^[A-Za-z0-9.\-+]+$")

_REQ = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"\s*(?:\[[^\]]*\])?\s*(?P<spec>[^;]*)"
)

# OSI classifier suffix -> SPDX id (only unambiguous mappings).
_CLASSIFIER_TO_SPDX = {
    "MIT License": "MIT",
    "Apache Software License": "Apache-2.0",
    "BSD License": "BSD-3-Clause",
    "ISC License (ISCL)": "ISC",
    "Python Software Foundation License": "PSF-2.0",
    "The Unlicense (Unlicense)": "Unlicense",
}

_SCOPE_RANK = {"required": 0, "optional": 1, "excluded": 2}


def _parse_requirement(raw: str) -> tuple[str, str]:
    """Split a PEP 508-ish requirement into (normalized name, specifier)."""
    m = _REQ.match(raw)
    if not m:
        raise ValueError(f"unparseable requirement: {raw!r}")
    name = m.group("name").lower().replace("_", "-")
    return name, m.group("spec").strip()


def _pinned_version(spec: str) -> str | None:
    """Return the exact version when the spec is a single ``==`` pin."""
    m = re.fullmatch(r"==\s*([^\s,]+)", spec)
    return m.group(1) if m else None


def _pypi_license(dist_name: str) -> str | None:
    """Best-effort SPDX license for an installed distribution, else None."""
    try:
        md = importlib.metadata.metadata(dist_name)
    except importlib.metadata.PackageNotFoundError:
        return None
    expr = md.get("License-Expression")
    if expr:
        return expr.strip() or None
    for classifier in md.get_all("Classifier") or []:
        if classifier.startswith("License :: OSI Approved :: "):
            suffix = classifier.rsplit("::", 1)[-1].strip()
            return _CLASSIFIER_TO_SPDX.get(suffix, suffix)
    lic = md.get("License")
    if lic and len(lic) < 60 and "\n" not in lic:
        return lic.strip() or None
    return None


def _license_entry(license_str: str | None) -> dict | None:
    if not license_str:
        return None
    key = "id" if _SPDX_ID.match(license_str) else "name"
    return {"license": {key: license_str}}


def _pypi_components() -> list[dict]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    project = data["project"]
    comps: list[dict] = []

    def add(raw: str, scope: str, group: str) -> None:
        name, spec = _parse_requirement(raw)
        version = _pinned_version(spec)
        purl = f"pkg:pypi/{name}@{version}" if version else f"pkg:pypi/{name}"
        comp: dict = {
            "type": "library",
            "bom-ref": purl,
            "name": name,
            "scope": scope,
            "purl": purl,
            "group": group,
            "properties": [
                {"name": "specifier", "value": spec or "*"},
                {"name": "ecosystem", "value": "pypi"},
            ],
        }
        if version:
            comp["version"] = version
        lic = _license_entry(_pypi_license(name))
        if lic:
            comp["licenses"] = [lic]
        comps.append(comp)

    for raw in project.get("dependencies", []):
        add(raw, "required", "python-runtime")
    for group_name, reqs in (project.get("optional-dependencies") or {}).items():
        scope = "excluded" if group_name == "dev" else "optional"
        for raw in reqs:
            add(raw, scope, f"python-{group_name}")
    return comps


def _npm_components() -> list[dict]:
    lock = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))
    comps: dict[str, dict] = {}
    for path, entry in lock.get("packages", {}).items():
        if not path:  # root package itself
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        version = entry.get("version")
        if not version:
            continue
        purl = f"pkg:npm/{name}@{version}"
        scope = "excluded" if entry.get("dev") else "required"
        comp = comps.get(purl)
        if comp is None:
            comp = {
                "type": "library",
                "bom-ref": purl,
                "name": name,
                "version": version,
                "scope": scope,
                "purl": purl,
                "group": "npm-dev" if entry.get("dev") else "npm-runtime",
                "properties": [{"name": "ecosystem", "value": "npm"}],
            }
            lic = _license_entry(entry.get("license"))
            if lic:
                comp["licenses"] = [lic]
            comps[purl] = comp
        elif comp["scope"] != scope:
            # Same name@version reachable via prod and dev paths: it ships
            # in prod installs, so required wins over excluded.
            if scope == "required":
                comp["scope"] = "required"
                comp["group"] = "npm-runtime"
    return list(comps.values())


def build_bom() -> dict:
    components = _pypi_components() + _npm_components()
    components.sort(key=lambda c: (_SCOPE_RANK.get(c["scope"], 9), c["purl"]))
    return {
        "bomFormat": "CycloneDX",
        "specVersion": SPEC_VERSION,
        "serialNumber": SERIAL,
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "bom-ref": "code-atlas-forge",
                "name": "code-atlas-forge",
                "version": "dynamic",
                "licenses": [{"license": {"id": "Apache-2.0"}}],
            },
            "properties": [
                {"name": "generator", "value": "scripts/generate_sbom.py (lf-0154)"},
                {"name": "scope", "value": "pinned runtime + optional + locked npm tree"},
            ],
        },
        "components": components,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; fail if --output differs from regenerated BOM",
    )
    args = parser.parse_args()

    rendered = json.dumps(build_bom(), indent=2, sort_keys=False) + "\n"
    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(f"STALE: {args.output} differs from regenerated BOM — run:", file=sys.stderr)
            print(f"  .venv/bin/python {Path(__file__).relative_to(ROOT)}", file=sys.stderr)
            return 1
        print(f"OK: {args.output} is fresh")
        return 0
    args.output.write_text(rendered, encoding="utf-8")
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
