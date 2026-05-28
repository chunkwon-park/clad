"""Per-session .mcp.json renderer for the clad bridge."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from .. import paths


def render(key: str, port: int) -> dict:
    """Return the dict to be JSON-serialized into .mcp.json."""
    return {
        "mcpServers": {
            "clad-bridge": {
                "command": sys.executable,
                "args": ["-m", "clad.bridge.mcp", key],
                "env": {
                    "CLAD_BRIDGE_URL": f"http://127.0.0.1:{port}",
                },
            }
        }
    }


def write(key: str, port: int) -> Path:
    """Write ``.mcp.json`` at ``clad.paths.mcp_config_path(key)`` (mode 0o600)."""
    p = paths.mcp_config_path(key)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(render(key, port), indent=2), encoding="utf-8")
    try:
        p.chmod(0o600)
    except OSError:
        pass
    return p
