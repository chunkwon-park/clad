"""Background asyncio task that auto-closes idle sessions (AC-F9 / Step 7a)."""
from __future__ import annotations

import asyncio
import time

from .. import config as config_mod


async def watch_idle(bridge: object, stop_event: asyncio.Event) -> None:
    """Every cfg.idle_check_interval_s, scan sessions; for non-keepalive
    sessions idle >= cfg.idle_timeout_s, close them.

    ``bridge`` is the Bridge instance from bridge.server.
    ``stop_event`` is set when the daemon is shutting down.
    """
    while not stop_event.is_set():
        # Single reload per tick (was twice before — once for interval, once for
        # timeout). Reading both from the same snapshot keeps the loop atomic
        # against mid-tick config edits.
        bridge.cfg = config_mod.reload_if_changed(bridge.cfg)  # type: ignore[attr-defined]
        interval = bridge.cfg.idle_check_interval_s  # type: ignore[attr-defined]
        idle_timeout = bridge.cfg.idle_timeout_s  # type: ignore[attr-defined]

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break  # stop_event was set — exit cleanly
        except asyncio.TimeoutError:
            pass

        now = time.time()

        for key, sess in list(bridge.sessions.items()):  # type: ignore[attr-defined]
            if sess.keepalive:
                continue
            idle_s = now - sess.last_activity_at
            if idle_s >= idle_timeout:
                idle_minutes = int(idle_s // 60)
                reason = f"idle {idle_minutes}m"
                bridge.log.info(  # type: ignore[attr-defined]
                    "auto-close %s after %.0fs idle", key, idle_s
                )
                try:
                    await bridge.close_session(key, reason=reason)  # type: ignore[attr-defined]
                except Exception as exc:  # pragma: no cover
                    bridge.log.warning(  # type: ignore[attr-defined]
                        "error closing idle session %s: %s", key, exc
                    )
