"""Terminal detection + tmux attach argv builder for -CC support."""
from __future__ import annotations

import os
from typing import Optional


def terminal_supports_control_mode(env: dict | None = None) -> bool:
    """True for iTerm2 or WezTerm (both implement tmux control mode)."""
    e = env if env is not None else os.environ
    tp = e.get("TERM_PROGRAM", "")
    if tp == "iTerm.app" or tp == "WezTerm":
        return True
    if e.get("LC_TERMINAL") == "iTerm2":
        return True
    return False


def detected_terminal(env: dict | None = None) -> str:
    e = env if env is not None else os.environ
    return e.get("TERM_PROGRAM") or e.get("LC_TERMINAL") or "unknown"


def resolve_attach_mode(
    cli_flag: Optional[str],
    config_value: str,
    env: dict | None = None,
) -> str:
    """Decide 'cc' or 'plain'.

    cli_flag: 'cc' | 'plain' | None
    config_value: 'auto' | 'cc' | 'plain'
    """
    if cli_flag in ("cc", "plain"):
        return cli_flag
    if config_value in ("cc", "plain"):
        return config_value
    return "cc" if terminal_supports_control_mode(env) else "plain"


def build_attach_argv(mode: str, session: str, pane_id: str) -> list[str]:
    """Build argv for os.execvp('tmux', ...).

    tmux accepts a literal ';' as a command separator when passed as its own arg.
    """
    if mode == "cc":
        base = ["tmux", "-CC", "attach-session", "-t", session]
    else:
        base = ["tmux", "attach-session", "-t", session]
    if pane_id:
        base += [";", "select-pane", "-t", pane_id]
    return base
