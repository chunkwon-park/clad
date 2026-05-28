"""Pure subprocess wrappers for tmux control."""
from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import List

from . import logger as _logger_mod
from .projects import tmux_session_name


class TmuxError(RuntimeError):
    """Raised when a tmux subprocess call fails."""


def _run(argv: List[str], check: bool = True) -> subprocess.CompletedProcess:
    log = _logger_mod.get()
    log.debug("tmux: %s", argv)
    try:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if check and result.returncode != 0:
            raise TmuxError(
                f"tmux command {argv!r} exited {result.returncode}: {result.stderr.strip()}"
            )
        return result
    except subprocess.CalledProcessError as exc:
        raise TmuxError(
            f"tmux command {argv!r} failed: {exc.stderr.strip() if exc.stderr else ''}"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise TmuxError(f"tmux command {argv!r} timed out") from exc


def has_session(name: str) -> bool:
    """Return True if a tmux session with this name exists."""
    result = _run(["tmux", "has-session", "-t", name], check=False)
    return result.returncode == 0


def ensure_project_session(project_root: Path) -> str:
    """Return session name; create with a single 'clad' window if missing."""
    name = tmux_session_name(project_root)
    if has_session(name):
        return name
    _run(["tmux", "new-session", "-d", "-s", name, "-n", "clad"])
    return name


def list_panes(session: str) -> List[dict]:
    """Return [{'pane_id': str, 'title': str}, ...]. Returns [] if session missing."""
    result = _run(
        ["tmux", "list-panes", "-t", f"{session}:clad", "-F", "#{pane_id}|#{pane_title}"],
        check=False,
    )
    if result.returncode != 0:
        return []
    panes = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|", 1)
        pane_id = parts[0]
        title = parts[1] if len(parts) > 1 else ""
        panes.append({"pane_id": pane_id, "title": title})
    return panes


def spawn_pane(session: str, tag: str, workdir: Path) -> str:
    """Return pane_id like '%12'. Sets pane title to tag.

    Reuses the lone untitled starter pane created by new-session; otherwise
    splits a new pane inside the 'clad' window and retiles.
    """
    existing = list_panes(session)

    if len(existing) == 1 and existing[0]["title"] == "":
        # Reuse the untitled starter pane
        pane_id = existing[0]["pane_id"]
        # cd to workdir (leading space avoids shell history; shlex.quote for safety)
        cd_cmd = f" cd {shlex.quote(str(workdir))}"
        _run(["tmux", "send-keys", "-t", pane_id, cd_cmd, "Enter"])
    else:
        # Split a new pane in the clad window
        result = _run(
            [
                "tmux",
                "split-window",
                "-t",
                f"{session}:clad",
                "-P",
                "-F",
                "#{pane_id}",
                "-c",
                str(workdir),
            ]
        )
        pane_id = result.stdout.strip()
        # Retile after split
        _run(["tmux", "select-layout", "-t", f"{session}:clad", "tiled"])

    # Set pane title
    _run(["tmux", "select-pane", "-t", pane_id, "-T", tag])
    return pane_id


def send_keys(pane_id: str, text: str, enter: bool = True) -> None:
    """Send keys to a pane. Appends Enter by default."""
    argv = ["tmux", "send-keys", "-t", pane_id, text]
    if enter:
        argv.append("Enter")
    _run(argv)


def send_literal(pane_id: str, text: str) -> None:
    """Send text literally (no shell expansion) — uses tmux send-keys -l."""
    _run(["tmux", "send-keys", "-t", pane_id, "-l", text])


def capture_pane(pane_id: str, lines: int = 200) -> str:
    """Return the last `lines` lines of pane output as a string."""
    result = _run(
        ["tmux", "capture-pane", "-p", "-t", pane_id, "-S", f"-{lines}"]
    )
    return result.stdout


def wait_for_pane_content(pane_id: str, pattern: str, timeout_s: float) -> bool:
    """Poll capture_pane every 0.5s; substring match for pattern.

    Returns True if found before timeout, False otherwise.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            content = capture_pane(pane_id)
        except TmuxError:
            return False
        if pattern in content:
            return True
        time.sleep(0.5)
    return False


def kill_pane(pane_id: str) -> None:
    """Kill a pane; swallow errors if already gone."""
    try:
        _run(["tmux", "kill-pane", "-t", pane_id])
    except TmuxError:
        pass


def pane_exists(pane_id: str) -> bool:
    """Return True if the given pane_id is visible in any session."""
    result = _run(["tmux", "list-panes", "-a", "-F", "#{pane_id}"], check=False)
    if result.returncode != 0:
        return False
    return pane_id in result.stdout.splitlines()
