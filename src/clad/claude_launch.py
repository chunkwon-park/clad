"""Launch Claude Code inside a tmux pane and handle init prompts."""
from __future__ import annotations

import shlex
import time
from pathlib import Path
from typing import List

from . import logger as _logger_mod
from . import tmux


def build_claude_argv(
    mcp_config_path: Path, permissions_mode: str = "skip"
) -> List[str]:
    """Return the argv list for launching Claude with the clad MCP config.

    The plan referenced ``--dangerously-load-development-channels`` from a
    legacy orchestrator, but that flag does not exist in current Claude Code
    (verified against claude 2.1.153). The MCP tools (`clad_get_prompt`,
    `clad_emit_token`, `clad_emit_done`) loaded via `--mcp-config` are the
    channel — no extra flag is needed.

    Appends ``--dangerously-skip-permissions`` when ``permissions_mode == 'skip'``.
    """
    argv = [
        "claude",
        "--mcp-config",
        str(mcp_config_path),
    ]
    if permissions_mode == "skip":
        argv.append("--dangerously-skip-permissions")
    return argv


def launch_claude(
    pane_id: str,
    workdir: Path,
    mcp_config_path: Path,
    permissions_mode: str = "skip",
) -> None:
    """cd to workdir and exec Claude with the right flags inside a tmux pane."""
    log = _logger_mod.get()
    argv = build_claude_argv(mcp_config_path, permissions_mode)
    cmd = f"cd {shlex.quote(str(workdir))} && {shlex.join(argv)}"
    log.debug("launch_claude pane=%s cmd=%s", pane_id, cmd)
    tmux.send_keys(pane_id, cmd, enter=True)


#: Substring that uniquely identifies Claude Code's trust-folder dialog.
#: Pattern verified against claude 2.1.153 UI capture.
_TRUST_MARKER = "1. Yes, I trust this folder"

#: Substrings that uniquely identify Claude Code's ready state. The footer
#: "bypass permissions on" line is shown only once the welcome box has
#: rendered AND the input cursor is interactive. "What's new" is the title
#: of the welcome side-panel, which only appears after init completes.
_READY_MARKERS = ("bypass permissions on", "What's new")


def handle_init_prompts(pane_id: str, timeout_s: float = 45.0) -> bool:
    """Drive Claude Code through the trust dialog and wait for the ready state.

    Returns True when the welcome screen is up and the input cursor is live;
    False on timeout. Verified against Claude Code 2.1.153.
    """
    log = _logger_mod.get()
    poll_interval = 0.7
    max_iters = max(1, int(timeout_s / poll_interval) + 1)
    trust_sent = False

    for i in range(max_iters):
        try:
            content = tmux.capture_pane(pane_id)
        except tmux.TmuxError:
            return False

        # Ready: the welcome box is rendered. Only treat as ready *after* the
        # trust step has cleared (the trust dialog itself contains "❯" too).
        if trust_sent and any(marker in content for marker in _READY_MARKERS):
            log.info("handle_init_prompts: ready state detected (iter %d)", i)
            return True

        # Some entry paths bypass the trust dialog entirely (e.g. previously
        # trusted folder). Accept ready immediately if the welcome screen is
        # visible without having seen the trust dialog at all.
        if not trust_sent and (_TRUST_MARKER not in content) and any(
            marker in content for marker in _READY_MARKERS
        ):
            log.info("handle_init_prompts: ready (no trust dialog) (iter %d)", i)
            return True

        # Trust dialog: the default selection is "Yes, I trust this folder"
        # (the `❯` cursor sits on line 1). Pressing Enter accepts it.
        if not trust_sent and _TRUST_MARKER in content:
            log.info("handle_init_prompts: trust dialog detected, sending Enter")
            tmux.send_keys(pane_id, "", enter=True)
            trust_sent = True
            time.sleep(1.0)
            continue

        # Other "Press Enter to continue" dialogs (older Claude flows).
        if "press enter to continue" in content.lower():
            log.info("handle_init_prompts: 'press enter' prompt detected")
            tmux.send_keys(pane_id, "", enter=True)
            time.sleep(poll_interval)
            continue

        time.sleep(poll_interval)

    log.info("handle_init_prompts: timed out after %.1fs", timeout_s)
    return False
