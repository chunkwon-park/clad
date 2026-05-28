"""CLI-side HTTP client for the clad bridge daemon.

Provides ``ensure_bridge_running()`` which auto-spawns the daemon if needed,
and thin wrappers around every bridge HTTP endpoint.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Generator

import requests

from . import paths


class BridgeError(RuntimeError):
    """Raised when the bridge cannot be reached or started."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_BASE_URL: str = ""  # populated by _init_base_url()


def _read_port() -> int | None:
    p = paths.bridge_port_file()
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _read_pid() -> int | None:
    p = paths.bridge_pid_file()
    if not p.exists():
        return None
    try:
        return int(p.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def _process_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _healthz(port: int, timeout: float = 2.0) -> bool:
    try:
        r = requests.get(f"http://127.0.0.1:{port}/healthz", timeout=timeout)
        return r.status_code == 200 and r.json().get("ok") is True
    except Exception:
        return False


def _url(path: str) -> str:
    port = _read_port()
    if not port:
        raise BridgeError("Bridge port file is missing — bridge may not be running")
    return f"http://127.0.0.1:{port}{path}"


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_bridge_running() -> tuple[int, int]:
    """Return (pid, port). Spawn ``python -m clad.bridge`` if not running.

    Waits up to 8 s for /healthz. Raises BridgeError on failure.
    """
    pid = _read_pid()
    port = _read_port()

    if pid is not None and _process_alive(pid) and port is not None:
        if _healthz(port):
            return pid, port

    # Bridge not running — spawn it detached
    cmd = [sys.executable, "-m", "clad.bridge"]
    subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )

    # Wait up to 8 s for the bridge to become healthy
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline:
        time.sleep(0.2)
        pid = _read_pid()
        port = _read_port()
        if pid is not None and port is not None and _healthz(port, timeout=1.0):
            return pid, port

    raise BridgeError(
        "clad-bridge did not start within 8 s. "
        "Run `clad doctor` for diagnostics."
    )


def post_prompt(key: str, prompt: str) -> dict:
    """POST /sessions/{key}/prompt."""
    r = requests.post(
        _url(f"/sessions/{key}/prompt"),
        json={"prompt": prompt},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def create_session(
    project: str,
    tag: str,
    keepalive: bool = False,
    workdir: str | None = None,
) -> dict:
    """POST /sessions — cold-start or reuse. May take up to ~60 s on cold start."""
    payload: dict = {"project": project, "tag": tag, "keepalive": keepalive}
    if workdir:
        payload["workdir"] = workdir
    r = requests.post(
        _url("/sessions"),
        json=payload,
        timeout=90,  # Cold start can take up to 45 s + margin
    )
    r.raise_for_status()
    return r.json()


def get_session(key: str) -> dict | None:
    """GET /sessions/{key}. Returns None if not found."""
    r = requests.get(_url(f"/sessions/{key}"), timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def delete_session(key: str, reason: str = "user") -> None:
    """DELETE /sessions/{key}."""
    r = requests.delete(
        _url(f"/sessions/{key}"),
        params={"reason": reason},
        timeout=30,
    )
    r.raise_for_status()


def list_sessions(
    all_projects: bool = False,
    project: str | None = None,
) -> list[dict]:
    """GET /sessions. Filter by project unless all_projects=True."""
    params: dict = {}
    if all_projects:
        params["all"] = "true"
    elif project:
        params["project"] = project
    r = requests.get(_url("/sessions"), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("sessions", [])


def stream(key: str, last_event_id: int = 0) -> Generator[dict, None, None]:
    """Yield SSE event dicts from GET /sessions/{key}/stream.

    Each yielded dict has shape:
        {"type": str, "data": ..., "ts": float, "id": int}

    Uses sseclient if available; otherwise implements a minimal line-based reader.

    Timeout: 10s connect, no read timeout — SSE streams can pause arbitrarily
    long between tokens while Claude is thinking. A stuck stream is recoverable
    with Ctrl+C, which the CLI converts into a clean exit.
    """
    url = _url(f"/sessions/{key}/stream")
    headers = {"Accept": "text/event-stream"}
    if last_event_id:
        headers["Last-Event-ID"] = str(last_event_id)

    params = {"last_event_id": last_event_id} if last_event_id else {}

    try:
        import sseclient  # type: ignore[import]
        _HAVE_SSECLIENT = True
    except ImportError:
        _HAVE_SSECLIENT = False

    try:
        r = requests.get(
            url,
            params=params,
            headers=headers,
            stream=True,
            timeout=(10, None),
        )
        r.raise_for_status()

        if _HAVE_SSECLIENT:
            client = sseclient.SSEClient(r)
            for event in client.events():
                try:
                    payload = json.loads(event.data)
                except (json.JSONDecodeError, AttributeError):
                    continue
                eid = int(event.id) if event.id else 0
                yield {"id": eid, **payload}
        else:
            # Minimal line-based SSE reader
            current_id: str = ""
            current_data: list[str] = []
            for raw_line in r.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line: str = raw_line
                if line == "":
                    # Blank line — dispatch event
                    if current_data:
                        data_str = "\n".join(current_data)
                        try:
                            payload = json.loads(data_str)
                        except json.JSONDecodeError:
                            payload = {"raw": data_str}
                        eid = int(current_id) if current_id else 0
                        yield {"id": eid, **payload}
                    current_id = ""
                    current_data = []
                elif line.startswith("id:"):
                    current_id = line[3:].strip()
                elif line.startswith("data:"):
                    current_data.append(line[5:].strip())
    except requests.exceptions.ReadTimeout as e:
        raise BridgeError(
            f"stream read timed out — bridge stopped sending data ({e})"
        ) from e
    except requests.exceptions.ConnectionError as e:
        raise BridgeError(
            f"stream connection lost — bridge unreachable or dropped the connection ({e})"
        ) from e
    except requests.exceptions.RequestException as e:
        raise BridgeError(f"stream request failed: {e}") from e
