"""Config at ~/.clad/config.yaml with mtime-based hot reload."""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from . import paths

DEFAULTS: dict[str, object] = {
    "idle_timeout_minutes": 10,
    "idle_check_interval_seconds": 30,
    "permissions_mode": "skip",   # 'skip' | 'prompt'
    "tmux_attach_mode": "auto",   # 'auto' | 'cc' | 'plain'
}


@dataclass
class Config:
    idle_timeout_minutes: int = 10
    idle_check_interval_seconds: int = 30
    permissions_mode: str = "skip"
    tmux_attach_mode: str = "auto"
    _mtime: float = field(default=0.0, repr=False)

    @property
    def idle_timeout_s(self) -> float:
        return float(self.idle_timeout_minutes) * 60.0

    @property
    def idle_check_interval_s(self) -> float:
        return float(self.idle_check_interval_seconds)

    def to_dict(self) -> dict:
        out: dict = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            out[f.name] = getattr(self, f.name)
        return out

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        kwargs = {}
        for f in fields(cls):
            if f.name.startswith("_"):
                continue
            if f.name in d:
                kwargs[f.name] = d[f.name]
        return cls(**kwargs)


def _path() -> Path:
    return paths.config_file()


def ensure_file() -> Path:
    """Create the config file with defaults if missing."""
    paths.ensure_layout()
    p = _path()
    if not p.exists():
        p.write_text(yaml.safe_dump(DEFAULTS, sort_keys=True), encoding="utf-8")
    return p


def load() -> Config:
    p = ensure_file()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        data = {}
    merged = {**DEFAULTS, **(data if isinstance(data, dict) else {})}
    cfg = Config.from_dict(merged)
    try:
        cfg._mtime = p.stat().st_mtime
    except OSError:
        cfg._mtime = 0.0
    return cfg


def reload_if_changed(cfg: Config) -> Config:
    """If the file mtime changed, return a freshly loaded Config; else return same."""
    p = _path()
    try:
        mtime = p.stat().st_mtime
    except FileNotFoundError:
        return cfg
    if mtime > cfg._mtime:
        return load()
    return cfg


def get(key: str) -> object:
    cfg = load()
    if not hasattr(cfg, key):
        raise KeyError(key)
    return getattr(cfg, key)


def _coerce(key: str, value: str) -> object:
    if key in ("idle_timeout_minutes", "idle_check_interval_seconds"):
        return int(value)
    return value


def set_value(key: str, value: str) -> object:
    if key not in DEFAULTS:
        raise KeyError(key)
    coerced = _coerce(key, value)
    p = ensure_file()
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    data[key] = coerced
    p.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")
    # Touch mtime so watcher reloads immediately
    os.utime(p, None)
    return coerced


def all_keys() -> list[str]:
    return list(DEFAULTS.keys())
