"""Bridge HTTP contract — exercise endpoints without tmux/claude.

We replace ``session_manager.create_or_get`` / ``close_session`` with stubs so
we never touch tmux. The Bridge + aiohttp app under test is otherwise the real
implementation.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

import pytest
import pytest_asyncio  # type: ignore  # noqa: F401  (registers plugin)
from aiohttp.test_utils import TestClient, TestServer

from clad import config as config_mod
from clad import state as state_mod
from clad.bridge import server as server_mod


@pytest.fixture
async def client(isolated_clad_home: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Return an aiohttp TestClient bound to a fresh Bridge."""
    cfg = config_mod.load()
    bridge = server_mod.Bridge(cfg=cfg, port=12345, pid=999)

    # Stub session_manager.create_or_get to avoid tmux + claude
    async def fake_create(bridge, key, project, tag, keepalive, workdir, cfg):
        rec = state_mod.SessionRecord(
            key=key,
            project=str(project),
            tag=tag,
            pane_id="%0",
            tmux_session=f"clad-fake",
            mcp_config_path="/dev/null",
            created_at=time.time(),
            last_activity_at=time.time(),
            keepalive=keepalive,
        )
        bridge.sessions[key] = rec
        bridge.persist(rec)
        return rec

    async def fake_close(bridge, key, reason="user"):
        bridge.publish(key, "auto_closed", {"reason": reason})
        bridge.sessions.pop(key, None)
        with state_mod.transaction() as st:
            st.sessions.pop(key, None)
        for q in bridge.subscribers.pop(key, []):
            try:
                q.put_nowait(None)
            except Exception:
                pass

    monkeypatch.setattr("clad.bridge.session_manager.create_or_get", fake_create)
    monkeypatch.setattr("clad.bridge.session_manager.close_session", fake_close)

    app = server_mod.create_app(bridge)
    async with TestServer(app) as ts:
        async with TestClient(ts) as c:
            yield c


async def test_healthz(client: TestClient) -> None:
    r = await client.get("/healthz")
    assert r.status == 200
    data = await r.json()
    assert data["ok"] is True
    assert data["port"] == 12345


async def test_create_session_then_get(client: TestClient) -> None:
    r = await client.post("/sessions", json={
        "project": "/tmp/proj-1", "tag": "auth", "keepalive": False,
    })
    assert r.status == 200
    data = await r.json()
    key = data["key"]
    assert data["created"] is True
    assert data["pane_id"] == "%0"

    r2 = await client.get(f"/sessions/{key}")
    assert r2.status == 200
    rec = await r2.json()
    assert rec["tag"] == "auth"


async def test_list_sessions_filter_by_project(client: TestClient) -> None:
    await client.post("/sessions", json={"project": "/p/a", "tag": "default"})
    await client.post("/sessions", json={"project": "/p/b", "tag": "default"})

    r = await client.get("/sessions", params={"project": "/p/a"})
    data = await r.json()
    assert len(data["sessions"]) == 1
    assert data["sessions"][0]["project"] == "/p/a"

    r = await client.get("/sessions", params={"all": "true"})
    data = await r.json()
    assert len(data["sessions"]) == 2


async def test_post_prompt_enqueues_and_returns_event_id(client: TestClient) -> None:
    await client.post("/sessions", json={"project": "/tmp/proj-2", "tag": "default"})
    import hashlib
    key = hashlib.sha1(b"/tmp/proj-2").hexdigest()[:10] + "-default"

    r = await client.post(f"/sessions/{key}/prompt", json={"prompt": "hello"})
    assert r.status == 200
    data = await r.json()
    assert data["accepted"] is True
    assert "event_id" in data


async def test_delete_session(client: TestClient) -> None:
    await client.post("/sessions", json={"project": "/tmp/proj-3", "tag": "default"})
    import hashlib
    key = hashlib.sha1(b"/tmp/proj-3").hexdigest()[:10] + "-default"

    r = await client.delete(f"/sessions/{key}")
    assert r.status == 200

    r2 = await client.get(f"/sessions/{key}")
    assert r2.status == 404


async def test_internal_mcp_token_publishes_to_sse(client: TestClient) -> None:
    """A POST /internal/mcp/{key}/token should publish into the ring buffer."""
    await client.post("/sessions", json={"project": "/tmp/proj-4", "tag": "default"})
    import hashlib
    key = hashlib.sha1(b"/tmp/proj-4").hexdigest()[:10] + "-default"

    # Fire a few tokens via the internal endpoint
    for t in ("hel", "lo!", " world"):
        r = await client.post(f"/internal/mcp/{key}/token", json={"text": t})
        assert r.status == 200

    # Publish a done event
    await client.post(f"/internal/mcp/{key}/done", json={"summary": "fin"})

    # Connect to SSE stream and read until 'done'
    r = await client.get(f"/sessions/{key}/stream")
    assert r.status == 200

    text = ""
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        chunk = await r.content.read(1024)
        if not chunk:
            break
        text += chunk.decode("utf-8", errors="replace")
        if '"type": "done"' in text or '"type":"done"' in text:
            break

    assert "hel" in text
    assert "lo!" in text
    assert "world" in text
    assert "done" in text


async def test_sse_last_event_id_skip(client: TestClient) -> None:
    """Subscribers with last_event_id should not see earlier buffered events."""
    await client.post("/sessions", json={"project": "/tmp/proj-5", "tag": "default"})
    import hashlib
    key = hashlib.sha1(b"/tmp/proj-5").hexdigest()[:10] + "-default"

    await client.post(f"/internal/mcp/{key}/token", json={"text": "OLD"})
    await client.post(f"/internal/mcp/{key}/done", json={})

    # Subscribe with a high last_event_id; replay should be empty, and the stream
    # should hang waiting for new events. We expect to time out.
    r = await client.get(f"/sessions/{key}/stream", params={"last_event_id": 999})
    text = ""
    deadline = time.monotonic() + 0.6
    while time.monotonic() < deadline:
        try:
            chunk = await asyncio.wait_for(r.content.read(64), timeout=0.2)
        except asyncio.TimeoutError:
            break
        if not chunk:
            break
        text += chunk.decode("utf-8", errors="replace")

    # No OLD token should appear
    assert "OLD" not in text
