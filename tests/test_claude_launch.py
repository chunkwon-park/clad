"""build_claude_argv shape."""
from __future__ import annotations

from pathlib import Path

from clad import claude_launch


def test_argv_loads_mcp_config() -> None:
    argv = claude_launch.build_claude_argv(Path("/tmp/x/.mcp.json"))
    assert argv[0] == "claude"
    assert "--mcp-config" in argv
    assert "/tmp/x/.mcp.json" in argv
    assert "--dangerously-skip-permissions" in argv
    # The legacy --dangerously-load-development-channels flag was removed —
    # claude 2.1.153 does not accept it.
    assert "--dangerously-load-development-channels" not in argv


def test_argv_respects_permissions_mode() -> None:
    argv = claude_launch.build_claude_argv(Path("/tmp/x/.mcp.json"), permissions_mode="prompt")
    assert "--dangerously-skip-permissions" not in argv
