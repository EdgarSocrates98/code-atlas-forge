"""SBOM freshness + license gate (lf-0154, cycle-8.1 Phase 11).

``sbom.json`` must be regenerable from the pinned sources
(``pyproject.toml`` + ``explorer/package-lock.json``) and every
production-scope component must carry a permissive license.

These tests read the *artifact* (sbom.json) against the *sources* so a
stale or hand-edited BOM fails loudly. Regenerate with::

    .venv/bin/python scripts/generate_sbom.py
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import generate_sbom  # noqa: E402

SBOM_PATH = ROOT / "sbom.json"
DEPENDABOT_PATH = ROOT / ".github" / "dependabot.yml"

# Permissive-only allowlist for production deps (GA gate). Anything else —
# GPL/AGPL/LGPL/MPL/EPL/SSPL or an unrecognized expression — fails.
PERMISSIVE_LICENSES = {
    "MIT",
    "MIT-0",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "0BSD",
    "BSD-1-Clause",
    "ISC",
    "Zlib",
    "Unlicense",
    "CC0-1.0",
    "PSF-2.0",
    "Python-2.0",
}

PROD_SCOPES = {"required"}


def _load_sbom() -> dict:
    return json.loads(SBOM_PATH.read_text(encoding="utf-8"))


def _expected_prod_purls() -> set[str]:
    """Prod component purls straight from the pinned sources."""
    expected: set[str] = set()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    for raw in project.get("dependencies", []):
        name, spec = generate_sbom._parse_requirement(raw)
        version = generate_sbom._pinned_version(spec)
        expected.add(f"pkg:pypi/{name}@{version}" if version else f"pkg:pypi/{name}")
    lock = json.loads((ROOT / "explorer" / "package-lock.json").read_text(encoding="utf-8"))
    for path, entry in lock.get("packages", {}).items():
        if not path or entry.get("dev"):
            continue
        name = path.rsplit("node_modules/", 1)[-1]
        if entry.get("version"):
            expected.add(f"pkg:npm/{name}@{entry['version']}")
    return expected


class TestSbomValid(unittest.TestCase):
    def test_is_cyclonedx_15_with_unique_refs(self) -> None:
        sbom = _load_sbom()
        self.assertEqual(sbom["bomFormat"], "CycloneDX")
        self.assertEqual(sbom["specVersion"], "1.5")
        self.assertTrue(sbom["serialNumber"].startswith("urn:uuid:"))
        comps = sbom["components"]
        refs = [c["bom-ref"] for c in comps]
        self.assertEqual(len(refs), len(set(refs)), "duplicate bom-refs in sbom.json")
        for comp in comps:
            self.assertIn(comp["scope"], {"required", "optional", "excluded"})
            self.assertTrue(comp["purl"].startswith("pkg:"))


class TestSbomFreshness(unittest.TestCase):
    def test_sbom_matches_regeneration(self) -> None:
        """sbom.json is byte-identical to a fresh regeneration."""
        expected = json.dumps(generate_sbom.build_bom(), indent=2) + "\n"
        actual = SBOM_PATH.read_text(encoding="utf-8")
        self.assertEqual(
            actual,
            expected,
            "sbom.json is stale — run: .venv/bin/python scripts/generate_sbom.py",
        )

    def test_prod_deps_covered_exactly(self) -> None:
        """Every prod dep (pyproject runtime + lock non-dev) is in the BOM,
        and no required-scope component is unaccounted for."""
        sbom = _load_sbom()
        bom_prod = {c["purl"] for c in sbom["components"] if c["scope"] in PROD_SCOPES}
        expected = _expected_prod_purls()
        missing = expected - bom_prod
        extra = bom_prod - expected
        self.assertFalse(missing, f"prod deps missing from sbom.json: {sorted(missing)}")
        self.assertFalse(extra, f"stale required-scope entries in sbom.json: {sorted(extra)}")
        self.assertEqual(len(bom_prod), len(expected))


class TestLicenseGate(unittest.TestCase):
    def _license_ids(self, comp: dict) -> list[str]:
        ids = []
        for wrap in comp.get("licenses", []):
            lic = wrap.get("license", {})
            ids.append(lic.get("id") or lic.get("name") or "")
        return [i for i in ids if i]

    def test_prod_licenses_all_permissive(self) -> None:
        sbom = _load_sbom()
        failures = []
        for comp in sbom["components"]:
            if comp["scope"] not in PROD_SCOPES:
                continue
            ids = self._license_ids(comp)
            if not ids:
                failures.append(f"{comp['purl']}: no license recorded")
            elif not any(i in PERMISSIVE_LICENSES for i in ids):
                failures.append(f"{comp['purl']}: non-permissive license {ids}")
        self.assertFalse(
            failures,
            "GA license gate — prod deps failing:\n  " + "\n  ".join(failures),
        )

    def test_optional_licenses_permissive_when_present(self) -> None:
        """Optional deps (e.g. sqlite-vec, not in a minimal install) keep a
        permissive license whenever one is recorded."""
        sbom = _load_sbom()
        failures = []
        for comp in sbom["components"]:
            if comp["scope"] != "optional":
                continue
            for lic_id in self._license_ids(comp):
                if lic_id not in PERMISSIVE_LICENSES:
                    failures.append(f"{comp['purl']}: {lic_id}")
        self.assertFalse(failures, "optional dep with non-permissive license: " + str(failures))


class TestDependabotCoverage(unittest.TestCase):
    """Dependabot must watch every pinned ecosystem: pip, GitHub Actions
    and the explorer npm tree (cycle-8 fixed npm coverage; keep it)."""

    def test_ecosystems_covered(self) -> None:
        text = DEPENDABOT_PATH.read_text(encoding="utf-8")
        entries = re.findall(
            r"package-ecosystem:\s*\"?([\w-]+)\"?\s*\n\s*directory:\s*\"?([^\"\n]+)\"?",
            text,
        )
        covered = {(eco.strip(), d.strip().strip('"')) for eco, d in entries}
        expected = {
            ("pip", "/"),
            ("github-actions", "/"),
            ("npm", "/explorer"),
        }
        self.assertTrue(
            expected.issubset(covered),
            f"dependabot.yml missing coverage; has {sorted(covered)}, needs {sorted(expected)}",
        )


if __name__ == "__main__":
    unittest.main()
