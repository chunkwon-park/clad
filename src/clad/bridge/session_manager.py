"""Session lifecycle management for the clad bridge (cold-start + close)."""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from .. import logger as logger_mod
from .. import state as state_mod
from .. import tmux as tmux_mod
from .. import claude_launch
from . import mcp_config as mcp_config_mod

_log = logger_mod.get("clad.session_manager")

BOOTSTRAP_INSTRUCTION = (
    "You are a worker inside the clad CLI. Loop forever: call clad_get_prompt; "
    "when you receive a prompt, complete it, emit results via clad_emit_token "
    "incrementally and clad_emit_done when finished, then loop. Begin now."
)


async def create_or_get(
    bridge: object,
    key: str,
    project: Path,
    tag: str,
    keepalive: bool,
    workdir: "Path | None",
    cfg: object,
) -> state_mod.SessionRecord:
    """Cold-start lifecycle. Serialize per-key with bridge.creation_locks[key].

    If an existing live record is found, reuse it.
    If the pane is dead, mark stale and recreate.
    If keepalive=True, set sticky on the record.
    """
    # Lazy-init the per-key lock
    locks: dict = bridge.creation_locks  # type: ignore[attr-defined]
    if key not in locks:
        locks[key] = asyncio.Lock()
    lock: asyncio.Lock = locks[key]

    async with lock:
        # Check for existing session
        existing: "state_mod.SessionRecord | None" = bridge.sessions.get(key)  # type: ignore[attr-defined]
        if existing is not None and not existing.stale:
            # Validate pane is still alive
            if tmux_mod.pane_exists(existing.pane_id):
                # Sticky keepalive — only set, never clear via this path
                if keepalive and not existing.keepalive:
                    existing.keepalive = True
                    bridge.persist(existing)  # type: ignore[attr-defined]
                return existing
            else:
                # Pane is dead — mark stale and fall through to recreate
                existing.stale = True
                bridge.persist(existing)  # type: ignore[attr-defined]

        # Cold-start
        effective_workdir = workdir or project

        # 1. Ensure tmux session for this project
        tmux_session = tmux_mod.ensure_project_session(project)

        # 2. Spawn a new pane
        pane_id = tmux_mod.spawn_pane(tmux_session, tag, effective_workdir)

        # 3. Write per-session .mcp.json
        mcp_path = mcp_config_mod.write(key, bridge.port)  # type: ignore[attr-defined]

        # 4. Launch Claude in the pane
        claude_launch.launch_claude(
            pane_id,
            effective_workdir,
            mcp_path,
            cfg.permissions_mode,  # type: ignore[attr-defined]
        )

        # 5. Handle init prompts (trust/confirm)
        ok = claude_launch.handle_init_prompts(pane_id, timeout_s=45.0)
        if not ok:
            tmux_mod.kill_pane(pane_id)
            raise RuntimeError(
                f"Claude did not reach ready state in pane {pane_id} "
                f"(session {key})"
            )

        # 6. Send bootstrap instruction so Claude starts polling the channel
        # Type bootstrap, then send Enter separately. A single send-keys with
        # both text and "Enter" races the TUI's input commit on long strings
        # and can leave the text stuck in Claude's input field.
        tmux_mod.send_keys(pane_id, BOOTSTRAP_INSTRUCTION, enter=False)
        await asyncio.sleep(0.5)
        tmux_mod.send_keys(pane_id, "", enter=True)
        _log.info("session %s: bootstrap submitted", key)

        # 7. Build and persist the record
        now = time.time()
        rec = state_mod.SessionRecord(
            key=key,
            project=str(project),
            tag=tag,
            pane_id=pane_id,
            tmux_session=tmux_session,
            mcp_config_path=str(mcp_path),
            created_at=now,
            last_activity_at=now,
            keepalive=keepalive,
            channel_id="clad-bridge",
        )
        bridge.sessions[key] = rec  # type: ignore[attr-defined]
        bridge.persist(rec)  # type: ignore[attr-defined]
        return rec


async def close_session(
    bridge: object,
    key: str,
    reason: str = "user",
) -> None:
    """Send /exit to pane, wait 3 s, kill pane, remove from state and subscribers."""
    sessions: dict = bridge.sessions  # type: ignore[attr-defined]
    rec = sessions.get(key)

    # 1. Publish auto_closed SSE event so subscribers see why
    bridge.publish(key, "auto_closed", {"reason": reason})  # type: ignore[attr-defined]

    # 2. Send /exit and wait, then kill
    if rec is not None and rec.pane_id:
        try:
            tmux_mod.send_keys(rec.pane_id, "/exit", enter=True)
            await asyncio.sleep(3)
            tmux_mod.kill_pane(rec.pane_id)
        except Exception:
            pass  # Pane may already be dead

    # 3. Remove from in-memory dict and persist deletion
    sessions.pop(key, None)
    with state_mod.transaction() as st:
        st.sessions.pop(key, None)

    # 4. Close all subscriber queues for this key
    subscribers: dict = bridge.subscribers  # type: ignore[attr-defined]
    for q in subscribers.pop(key, []):
        try:
            q.put_nowait(None)  # sentinel to unblock SSE handlers
        except Exception:
            pass
