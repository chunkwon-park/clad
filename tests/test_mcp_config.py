"""Per-session .mcp.json rendering."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from clad.bridge import mcp_config


def test_render_shape() -> None:
    d = mcp_config.render("abcdef0123-default", 54321)
    assert "mcpServers" in d
    bridge = d["mcpServers"]["clad-bridge"]
    assert bridge["command"] == sys.executable
    assert bridge["args"] == ["-m", "clad.bridge.mcp", "abcdef0123-default"]
    assert bridge["env"]["CLAD_BRIDGE_URL"] == "http://127.0.0.1:54321"


def test_write_creates_file(isolated_clad_home: Path) -> None:
    p = mcp_config.write("k-tag", 8081)
    assert p.exists()
    data = json.loads(p.read_text())
    assert data["mcpServers"]["clad-bridge"]["env"]["CLAD_BRIDGE_URL"] == "http://127.0.0.1:8081"
    # Parent directory was created
    assert p.parent.name == "k-tag"
