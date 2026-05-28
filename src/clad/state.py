"""Atomic JSON state file at ~/.clad/state.json with fcntl flock."""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator

from . import paths


@dataclass
class SessionRecord:
    key: str
    project: str  # absolute path
    tag: str
    pane_id: str = ""          # e.g. "%12"
    tmux_session: str = ""     # e.g. "clad-a3f1c92b08"
    mcp_config_path: str = ""
    created_at: float | None = None
    last_prompt_at: float | None = None
    last_activity_at: float = field(default_factory=time.time)
    keepalive: bool = False
    channel_id: str = ""
    stale: bool = False
    last_prompt: str = ""

    def touch(self, now: float | None = None) -> None:
        self.last_activity_at = now if now is not None else time.time()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionRecord":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in allowed})


@dataclass
class State:
    sessions: dict[str, SessionRecord] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"sessions": {k: v.to_dict() for k, v in self.sessions.items()}}

    @classmethod
    def from_dict(cls, d: dict) -> "State":
        raw = d.get("sessions") or {}
        return cls(sessions={k: SessionRecord.from_dict(v) for k, v in raw.items()})


@contextlib.contextmanager
def _flock() -> Iterator[None]:
    """Acquire an exclusive file lock on ``~/.clad/state.lock``.

    Uses ``O_NOFOLLOW`` to refuse a symlinked lockfile, and ``0o600`` so the
    lockfile is owner-private (only contents are an empty inode anyway).
    """
    paths.ensure_layout()
    lock_path = paths.state_lock()
    flags = os.O_CREAT | os.O_RDWR
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(str(lock_path), flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def load() -> State:
    p = paths.state_file()
    if not p.exists():
        return State()
    try:
        with p.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
        return State.from_dict(data)
    except (json.JSONDecodeError, OSError):
        return State()


def save(state: State) -> None:
    paths.ensure_layout()
    target = paths.state_file()
    payload = json.dumps(state.to_dict(), indent=2, sort_keys=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix="state.", suffix=".json.tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp_path)
        raise


@contextlib.contextmanager
def transaction() -> Iterator[State]:
    """Read state, yield it for mutation, then atomically write under flock."""
    with _flock():
        st = load()
        yield st
        save(st)


def read() -> State:
    """Read without locking — for read-only callers that don't mutate."""
    with _flock():
        return load()
