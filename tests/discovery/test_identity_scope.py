"""lf-0058 Phase 0.4 — identidade multi-root: sub-roots de monorepo."""

from __future__ import annotations

from code_atlas.discovery.identity import identity_key
from code_atlas.discovery.models import GitInfo


def _git(toplevel: str) -> GitInfo:
    return GitInfo(
        available=True, is_repo=True, branch="main",
        head_sha="abc", remote_url="git@x:mono.git",
        root_commit="root1", toplevel=toplevel)


def test_repo_root_keeps_legacy_key(tmp_path) -> None:
    mono = tmp_path / "mono"
    mono.mkdir()
    key = identity_key(_git(str(mono)), mono)
    assert key == "git-remote:git@x:mono.git"  # sem sufixo — retrocompatível


def test_subroot_gets_scoped_key(tmp_path) -> None:
    mono = tmp_path / "mono"
    (mono / "apps" / "web").mkdir(parents=True)
    key = identity_key(_git(str(mono)), mono / "apps" / "web")
    assert key == "git-remote:git@x:mono.git@apps/web"


def test_sibling_roots_distinct(tmp_path) -> None:
    mono = tmp_path / "mono"
    (mono / "a").mkdir(parents=True)
    (mono / "b").mkdir(parents=True)
    g = _git(str(mono))
    assert identity_key(g, mono / "a") != identity_key(g, mono / "b")


def test_relocation_preserves_scoped_key(tmp_path) -> None:
    # Mesmo monorepo clonado em outro path: relpath igual → mesma chave.
    m1 = tmp_path / "c1" / "mono"
    m2 = tmp_path / "c2" / "mono"
    (m1 / "pkg").mkdir(parents=True)
    (m2 / "pkg").mkdir(parents=True)
    k1 = identity_key(_git(str(m1)), m1 / "pkg")
    k2 = identity_key(_git(str(m2)), m2 / "pkg")
    assert k1 == k2


def test_no_remote_subroot_uses_root_commit(tmp_path) -> None:
    mono = tmp_path / "mono"
    (mono / "sub").mkdir(parents=True)
    g = GitInfo(available=True, is_repo=True, root_commit="r9",
                toplevel=str(mono))
    assert identity_key(g, mono / "sub") == "git-root:r9@sub"


def test_path_fallback_unchanged(tmp_path) -> None:
    p = tmp_path / "plain"
    p.mkdir()
    g = GitInfo(available=True, is_repo=False)
    assert identity_key(g, p) == f"path:{p.resolve()}"
