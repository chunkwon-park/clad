"""Paths layout uses CLAD_HOME override and creates expected subdirs."""
from __future__ import annotations

from pathlib import Path

from clad import paths


def test_layout_creates_subdirs(isolated_clad_home: Path) -> None:
    paths.ensure_layout()
    assert isolated_clad_home.is_dir()
    assert (isolated_clad_home / "logs").is_dir()
    assert (isolated_clad_home / "logs" / "sessions").is_dir()
    assert (isolated_clad_home / "mcp").is_dir()


def test_state_and_config_paths(isolated_clad_home: Path) -> None:
    assert paths.state_file() == isolated_clad_home / "state.json"
    assert paths.config_file() == isolated_clad_home / "config.yaml"
    assert paths.bridge_pid_file() == isolated_clad_home / "bridge.pid"
    assert paths.bridge_port_file() == isolated_clad_home / "bridge.port"


def test_session_log_path(isolated_clad_home: Path) -> None:
    p = paths.session_log("abc123-auth")
    assert p == isolated_clad_home / "logs" / "sessions" / "abc123-auth.jsonl"


def test_mcp_config_path(isolated_clad_home: Path) -> None:
    p = paths.mcp_config_path("k-default")
    assert p == isolated_clad_home / "mcp" / "k-default" / ".mcp.json"
