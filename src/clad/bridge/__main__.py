"""Daemon entry point: ``python -m clad.bridge``.

Supports ``--foreground`` for debugging. Without it, does a classic
double-fork detach before starting the aiohttp server.
"""
from __future__ import annotations

import asyncio
import os
import signal
import socket
import sys

from .. import config as config_mod
from .. import logger as logger_mod
from .. import paths
from .idle_watcher import watch_idle
from .server import Bridge, create_app


def _write_pid(port: int | None = None) -> None:
    paths.ensure_layout()
    paths.bridge_pid_file().write_text(str(os.getpid()), encoding="utf-8")
    if port is not None:
        paths.bridge_port_file().write_text(str(port), encoding="utf-8")


def _cleanup_pid_port() -> None:
    for p in (paths.bridge_pid_file(), paths.bridge_port_file()):
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _double_fork_detach() -> None:
    """Classic Unix double-fork to fully detach from the controlling terminal."""
    # First fork
    pid = os.fork()
    if pid > 0:
        os._exit(0)  # Parent exits

    os.setsid()  # New session, no controlling terminal

    # Second fork — prevent re-acquiring a controlling terminal
    pid = os.fork()
    if pid > 0:
        os._exit(0)

    # Redirect stdio to /dev/null
    devnull = os.open(os.devnull, os.O_RDWR)
    for fd in (0, 1, 2):
        try:
            os.dup2(devnull, fd)
        except OSError:
            pass
    os.close(devnull)

    os.chdir("/")


async def _run_server(foreground: bool) -> None:
    cfg = config_mod.load()
    log = logger_mod.setup(file_log=not foreground)

    # Pick a free port by binding temporarily
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    pid = os.getpid()
    _write_pid(port)
    log.info("clad-bridge starting pid=%d port=%d", pid, port)

    bridge = Bridge(cfg=cfg, port=port, pid=pid)
    bridge.load_from_disk()

    app = create_app(bridge)

    from aiohttp import web

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()

    log.info("clad-bridge listening on http://127.0.0.1:%d", port)

    stop_event = asyncio.Event()

    def _on_signal() -> None:
        log.info("clad-bridge received shutdown signal")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _on_signal)
        except (NotImplementedError, RuntimeError):
            pass  # Windows or restricted env

    # Start idle watcher as a background task
    idle_task = asyncio.create_task(watch_idle(bridge, stop_event))

    # Wait for shutdown signal
    await stop_event.wait()

    log.info("clad-bridge shutting down")
    idle_task.cancel()
    try:
        await idle_task
    except asyncio.CancelledError:
        pass

    await runner.cleanup()


def main() -> None:
    foreground = "--foreground" in sys.argv

    if not foreground:
        _double_fork_detach()

    try:
        asyncio.run(_run_server(foreground=foreground))
    finally:
        _cleanup_pid_port()


if __name__ == "__main__":
    main()
