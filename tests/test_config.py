"""Config YAML load/set/hot-reload."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from clad import config


def test_config_default_values(isolated_clad_home: Path) -> None:
    cfg = config.load()
    assert cfg.idle_timeout_minutes == 10
    assert cfg.idle_check_interval_seconds == 30
    assert cfg.permissions_mode == "skip"
    assert cfg.tmux_attach_mode == "auto"
    assert cfg.idle_timeout_s == 600.0


def test_config_set_and_get(isolated_clad_home: Path) -> None:
    new = config.set_value("idle_timeout_minutes", "5")
    assert new == 5
    assert config.get("idle_timeout_minutes") == 5

    config.set_value("tmux_attach_mode", "plain")
    cfg = config.load()
    assert cfg.tmux_attach_mode == "plain"


def test_config_set_unknown_key_raises(isolated_clad_home: Path) -> None:
    with pytest.raises(KeyError):
        config.set_value("does_not_exist", "x")


def test_config_hot_reload(isolated_clad_home: Path) -> None:
    cfg = config.load()
    assert cfg.idle_timeout_minutes == 10
    time.sleep(0.05)
    config.set_value("idle_timeout_minutes", "3")
    new_cfg = config.reload_if_changed(cfg)
    assert new_cfg.idle_timeout_minutes == 3


def test_config_file_created_with_defaults(isolated_clad_home: Path) -> None:
    config.ensure_file()
    path = isolated_clad_home / "config.yaml"
    assert path.exists()
    text = path.read_text()
    assert "idle_timeout_minutes" in text
