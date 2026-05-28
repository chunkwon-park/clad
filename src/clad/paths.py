"""Filesystem layout for ~/.clad/."""
from __future__ import annotations

import os
from pathlib import Path


def _root() -> Path:
    override = os.environ.get("CLAD_HOME")
    if override:
        return Path(override).expanduser()
    return Path("~/.clad").expanduser()


def state_dir() -> Path:
    return _root()


def state_file() -> Path:
    return _root() / "state.json"


def state_lock() -> Path:
    return _root() / "state.lock"


def config_file() -> Path:
    return _root() / "config.yaml"


def bridge_pid_file() -> Path:
    return _root() / "bridge.pid"


def bridge_port_file() -> Path:
    return _root() / "bridge.port"


def logs_dir() -> Path:
    return _root() / "logs"


def bridge_log() -> Path:
    return logs_dir() / "bridge.log"


def session_log(key: str) -> Path:
    return logs_dir() / "sessions" / f"{key}.jsonl"


def mcp_dir() -> Path:
    return _root() / "mcp"


def mcp_config_path(key: str) -> Path:
    return mcp_dir() / key / ".mcp.json"


def ensure_layout() -> None:
    """Create ~/.clad/{logs/sessions,mcp} if missing."""
    for p in (
        _root(),
        logs_dir(),
        logs_dir() / "sessions",
        mcp_dir(),
    ):
        p.mkdir(parents=True, exist_ok=True)
