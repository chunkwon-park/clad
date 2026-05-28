"""Atomic JSON state with fcntl flock."""
from __future__ import annotations

import threading
import time
from pathlib import Path

from clad import state


def test_state_roundtrip(isolated_clad_home: Path) -> None:
    with state.transaction() as st:
        st.sessions["k1"] = state.SessionRecord(
            key="k1", project="/proj", tag="default", pane_id="%0",
            tmux_session="clad-abc",
        )
    out = state.read()
    assert "k1" in out.sessions
    rec = out.sessions["k1"]
    assert rec.tag == "default"
    assert rec.pane_id == "%0"


def test_state_touch_updates_activity() -> None:
    rec = state.SessionRecord(key="k", project="/p", tag="t")
    before = rec.last_activity_at
    time.sleep(0.01)
    rec.touch()
    assert rec.last_activity_at > before


def test_state_concurrent_writes_no_corruption(isolated_clad_home: Path) -> None:
    """Two threads × 50 writes each: state file must remain valid JSON
    and contain at least all the unique keys written."""
    errors: list[Exception] = []

    def writer(prefix: str) -> None:
        try:
            for i in range(50):
                with state.transaction() as st:
                    st.sessions[f"{prefix}-{i}"] = state.SessionRecord(
                        key=f"{prefix}-{i}",
                        project=f"/proj/{prefix}",
                        tag=str(i),
                    )
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=writer, args=("a",))
    t2 = threading.Thread(target=writer, args=("b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert errors == []
    final = state.read()
    # Both threads' final writes should be visible
    assert "a-49" in final.sessions
    assert "b-49" in final.sessions


def test_state_missing_file_returns_empty(isolated_clad_home: Path) -> None:
    out = state.read()
    assert out.sessions == {}
