"""Pytest fixtures — isolate every test in a temp ~/.clad."""
from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_clad_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ~/.clad to a per-test temp dir via CLAD_HOME."""
    home = tmp_path / "clad_home"
    home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("CLAD_HOME", str(home))
    return home
