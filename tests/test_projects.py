"""Project root + session key tests."""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from clad import projects


def test_session_key_deterministic() -> None:
    root = Path("/some/project")
    k1 = projects.session_key(root, "auth")
    k2 = projects.session_key(root, "auth")
    assert k1 == k2
    assert k1.endswith("-auth")
    # First 10 hex chars + dash + tag
    head, _, tag = k1.partition("-")
    assert len(head) == 10
    assert tag == "auth"


def test_session_key_different_projects_collide_only_by_chance() -> None:
    a = projects.session_key(Path("/proj/a"), "default")
    b = projects.session_key(Path("/proj/b"), "default")
    assert a != b


def test_session_key_rejects_bad_tag() -> None:
    with pytest.raises(ValueError):
        projects.session_key(Path("/x"), "")
    with pytest.raises(ValueError):
        projects.session_key(Path("/x"), "with/slash")
    with pytest.raises(ValueError):
        projects.session_key(Path("/x"), "null\x00byte")


def test_tmux_session_name_starts_with_clad() -> None:
    name = projects.tmux_session_name(Path("/some/project"))
    assert name.startswith("clad-")
    assert len(name) == len("clad-") + 10


def test_resolve_project_root_in_git_repo(tmp_path: Path) -> None:
    repo = tmp_path / "myrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    sub = repo / "src" / "deep"
    sub.mkdir(parents=True)
    resolved = projects.resolve_project_root(sub)
    assert resolved == repo.resolve()


def test_resolve_project_root_falls_back_to_cwd(tmp_path: Path) -> None:
    no_git = tmp_path / "no_git"
    no_git.mkdir()
    resolved = projects.resolve_project_root(no_git)
    assert resolved == no_git.resolve()
