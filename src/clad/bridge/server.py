"""aiohttp HTTP+SSE server and Bridge class for the clad bridge daemon."""
from __future__ import annotations

import asyncio
import json
import time
from collections import deque
from pathlib import Path
from typing import Any

from aiohttp import web

from .. import config as config_mod
from .. import logger as logger_mod
from .. import paths
from .. import projects as projects_mod
from .. import state as state_mod
from . import session_manager

#: App-key used to attach the Bridge instance to the aiohttp Application
#: (typed key — aiohttp 3.9+ recommends this over plain string keys).
BRIDGE_KEY: web.AppKey["Bridge"] = web.AppKey("bridge", object)  # type: ignore[type-arg]


class Bridge:
    """Central state object shared by all HTTP handlers and background tasks."""

    def __init__(self, cfg: config_mod.Config, port: int, pid: int) -> None:
        self.cfg = cfg
        self.port = port
        self.pid = pid
        self.log = logger_mod.get("clad.bridge")

        # In-memory mirror of state file
        self.sessions: dict[str, state_mod.SessionRecord] = {}

        # Per-session SSE ring buffer (maxlen=1000)
        self.event_buffers: dict[str, deque] = {}
        self.event_id_counters: dict[str, int] = {}

        # Per-session prompt queues (MCP clad_get_prompt blocks on these)
        self.pending_prompts: dict[str, asyncio.Queue] = {}

        # Per-key creation locks (R-4: serialise concurrent cold-starts)
        self.creation_locks: dict[str, asyncio.Lock] = {}

        # Per-session SSE subscriber queues for fanout
        self.subscribers: dict[str, list[asyncio.Queue]] = {}

        # Back-reference to session_manager module (set after construction)
        self.session_manager = session_manager

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------

    def load_from_disk(self) -> None:
        """Populate in-memory sessions from the state file."""
        st = state_mod.read()
        self.sessions = dict(st.sessions)

    def persist(self, record: state_mod.SessionRecord) -> None:
        """Atomically write a single session record to the state file."""
        with state_mod.transaction() as st:
            st.sessions[record.key] = record

    # ------------------------------------------------------------------
    # Event ring buffer + SSE fanout
    # ------------------------------------------------------------------

    def _ensure_buffers(self, key: str) -> None:
        if key not in self.event_buffers:
            self.event_buffers[key] = deque(maxlen=1000)
        if key not in self.event_id_counters:
            self.event_id_counters[key] = 0

    def publish(self, key: str, event_type: str, data: Any) -> int:
        """Append event to ring buffer and fanout to all SSE subscribers.

        Returns the assigned event id.
        """
        self._ensure_buffers(key)
        self.event_id_counters[key] += 1
        event_id = self.event_id_counters[key]
        event = {
            "id": event_id,
            "type": event_type,
            "data": data,
            "ts": time.time(),
        }
        self.event_buffers[key].append(event)

        # Fanout to subscribers
        for q in list(self.subscribers.get(key, [])):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # Slow subscriber; drop

        return event_id

    def _get_buffered_since(self, key: str, last_event_id: int) -> list[dict]:
        """Return all buffered events with id > last_event_id."""
        self._ensure_buffers(key)
        return [e for e in self.event_buffers[key] if e["id"] > last_event_id]

    # ------------------------------------------------------------------
    # Prompt queue helpers
    # ------------------------------------------------------------------

    def get_or_create_queue(self, key: str) -> asyncio.Queue:
        """Lazy-init and return the prompt queue for key."""
        if key not in self.pending_prompts:
            self.pending_prompts[key] = asyncio.Queue()
        return self.pending_prompts[key]

    def enqueue_prompt(self, key: str, prompt: str) -> None:
        """Put prompt into the per-key queue and update activity timestamp."""
        q = self.get_or_create_queue(key)
        q.put_nowait(prompt)
        self.touch(key)

    def touch(self, key: str) -> None:
        """Reset last_activity_at for the session and persist."""
        rec = self.sessions.get(key)
        if rec is not None:
            rec.touch()
            self.persist(rec)

    # ------------------------------------------------------------------
    # Session close (used by idle_watcher + DELETE handler)
    # ------------------------------------------------------------------

    async def close_session(self, key: str, reason: str = "user") -> None:
        """Delegate to session_manager.close_session."""
        await self.session_manager.close_session(self, key, reason=reason)


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------

def _append_session_log(key: str, entry: dict) -> None:
    """Append a JSON line to the per-session log file."""
    try:
        log_path = paths.session_log(key)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except OSError:
        pass


async def _handle_post_prompt(request: web.Request) -> web.Response:
    """POST /sessions/{key}/prompt — enqueue a prompt."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="invalid JSON body")

    prompt = body.get("prompt")
    if not isinstance(prompt, str):
        raise web.HTTPBadRequest(reason="'prompt' field (string) is required")

    bridge.enqueue_prompt(key, prompt)

    # Update last_prompt fields in session record
    rec = bridge.sessions.get(key)
    if rec is not None:
        rec.last_prompt_at = time.time()
        rec.last_prompt = prompt
        bridge.persist(rec)

    event_id = bridge.event_id_counters.get(key, 0)
    _append_session_log(key, {
        "type": "prompt_received",
        "prompt": prompt,
        "ts": time.time(),
    })
    return web.json_response({"accepted": True, "event_id": event_id})


async def _handle_get_stream(request: web.Request) -> web.StreamResponse:
    """GET /sessions/{key}/stream — SSE stream."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]

    # Parse last_event_id from query string or header
    try:
        last_event_id = int(
            request.rel_url.query.get("last_event_id")
            or request.headers.get("Last-Event-ID")
            or "0"
        )
    except (TypeError, ValueError):
        last_event_id = 0

    resp = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await resp.prepare(request)

    async def write_event(event: dict) -> None:
        eid = event["id"]
        payload = json.dumps({
            "type": event["type"],
            "data": event["data"],
            "ts": event["ts"],
        })
        frame = f"id: {eid}\nevent: message\ndata: {payload}\n\n"
        await resp.write(frame.encode("utf-8"))

    # Subscribe FIRST so any event published during replay is captured into our
    # queue (would otherwise be lost in the gap between snapshot and subscribe).
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    if key not in bridge.subscribers:
        bridge.subscribers[key] = []
    bridge.subscribers[key].append(q)

    # Replay buffered events since last_event_id, recording IDs so the live
    # loop can de-dupe events that crossed the subscribe-vs-replay boundary.
    replayed_ids: set[int] = set()
    for event in bridge._get_buffered_since(key, last_event_id):
        replayed_ids.add(event["id"])
        await write_event(event)

    try:
        while True:
            event = await q.get()
            if event is None:
                break  # close-session sentinel
            if event["id"] in replayed_ids:
                continue
            await write_event(event)
            if event.get("type") in ("done", "auto_closed"):
                break
    except (asyncio.CancelledError, ConnectionResetError):
        pass
    finally:
        subs = bridge.subscribers.get(key, [])
        try:
            subs.remove(q)
        except ValueError:
            pass

    return resp


async def _handle_get_session(request: web.Request) -> web.Response:
    """GET /sessions/{key} — session metadata."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]
    rec = bridge.sessions.get(key)
    if rec is None:
        raise web.HTTPNotFound(reason=f"session {key!r} not found")
    return web.json_response(rec.to_dict())


async def _handle_delete_session(request: web.Request) -> web.Response:
    """DELETE /sessions/{key} — close a session."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]
    reason = request.rel_url.query.get("reason", "user")
    await bridge.close_session(key, reason=reason)
    return web.json_response({"closed": True})


async def _handle_post_sessions(request: web.Request) -> web.Response:
    """POST /sessions — cold-start or reuse a session."""
    bridge: Bridge = request.app["bridge"]

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="invalid JSON body")

    project_str = body.get("project")
    tag = body.get("tag", "default")
    keepalive = bool(body.get("keepalive", False))
    workdir_str = body.get("workdir")

    if not project_str:
        raise web.HTTPBadRequest(reason="'project' field is required")

    project = Path(project_str)
    workdir = Path(workdir_str) if workdir_str else None

    # Single source of truth for the key derivation
    key = projects_mod.session_key(project, tag)

    existing = bridge.sessions.get(key)
    created = existing is None or existing.stale

    rec = await session_manager.create_or_get(
        bridge=bridge,
        key=key,
        project=project,
        tag=tag,
        keepalive=keepalive,
        workdir=workdir,
        cfg=bridge.cfg,
    )

    return web.json_response({
        "key": rec.key,
        "created": created,
        "tmux_session": rec.tmux_session,
        "pane_id": rec.pane_id,
    })


async def _handle_list_sessions(request: web.Request) -> web.Response:
    """GET /sessions — list sessions."""
    bridge: Bridge = request.app["bridge"]
    all_flag = request.rel_url.query.get("all", "").lower() in ("true", "1", "yes")
    project_filter = request.rel_url.query.get("project")

    sessions = list(bridge.sessions.values())
    if not all_flag and project_filter:
        sessions = [s for s in sessions if s.project == project_filter]

    return web.json_response({"sessions": [s.to_dict() for s in sessions]})


async def _handle_healthz(request: web.Request) -> web.Response:
    """GET /healthz."""
    bridge: Bridge = request.app["bridge"]
    return web.json_response({"ok": True, "pid": bridge.pid, "port": bridge.port})


# ---------------------------------------------------------------------------
# Internal MCP endpoints
# ---------------------------------------------------------------------------

async def _handle_mcp_next_prompt(request: web.Request) -> web.Response:
    """GET /internal/mcp/{key}/next-prompt — long-poll for next prompt (up to 30s)."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]
    q = bridge.get_or_create_queue(key)

    try:
        prompt = await asyncio.wait_for(q.get(), timeout=30.0)
        _append_session_log(key, {"type": "prompt_delivered", "prompt": prompt, "ts": time.time()})
        bridge.touch(key)
        return web.json_response({"prompt": prompt})
    except asyncio.TimeoutError:
        return web.json_response({"prompt": None})


async def _handle_mcp_token(request: web.Request) -> web.Response:
    """POST /internal/mcp/{key}/token — publish a streaming token."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]

    try:
        body = await request.json()
    except Exception:
        raise web.HTTPBadRequest(reason="invalid JSON body")

    text = body.get("text", "")
    bridge.publish(key, "token", text)
    bridge.touch(key)
    _append_session_log(key, {"type": "token", "data": text, "ts": time.time()})
    return web.json_response({"ok": True})


async def _handle_mcp_done(request: web.Request) -> web.Response:
    """POST /internal/mcp/{key}/done — signal completion."""
    bridge: Bridge = request.app["bridge"]
    key = request.match_info["key"]

    try:
        body = await request.json()
    except Exception:
        body = {}

    summary = body.get("summary") if isinstance(body, dict) else None
    data: dict = {}
    if summary is not None:
        data["summary"] = summary
    bridge.publish(key, "done", data)
    bridge.touch(key)
    _append_session_log(key, {"type": "done", "data": data, "ts": time.time()})
    return web.json_response({"ok": True})


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(bridge: Bridge) -> web.Application:
    """Wire all routes and return the aiohttp Application."""
    app = web.Application()
    app["bridge"] = bridge

    app.router.add_post("/sessions/{key}/prompt", _handle_post_prompt)
    app.router.add_get("/sessions/{key}/stream", _handle_get_stream)
    app.router.add_get("/sessions/{key}", _handle_get_session)
    app.router.add_delete("/sessions/{key}", _handle_delete_session)
    app.router.add_post("/sessions", _handle_post_sessions)
    app.router.add_get("/sessions", _handle_list_sessions)
    app.router.add_get("/healthz", _handle_healthz)

    # Internal MCP endpoints
    app.router.add_get("/internal/mcp/{key}/next-prompt", _handle_mcp_next_prompt)
    app.router.add_post("/internal/mcp/{key}/token", _handle_mcp_token)
    app.router.add_post("/internal/mcp/{key}/done", _handle_mcp_done)

    return app
