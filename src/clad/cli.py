"""clad CLI entry point — Click sub-commands."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from . import __version__, bridge_client, config, logger, paths, projects, terminal
from .bridge_client import BridgeError

console = Console()
log = logger.setup()

# Known sub-commands at the group level — used by ``main`` to decide whether to
# inject a synthetic ``prompt`` first argument when the user types
# ``clad "some prompt" -t auth``.
_SUBCOMMANDS = {"prompt", "list", "close", "attach", "logs", "doctor", "config"}
_GROUP_FLAGS = {"-h", "--help", "--version"}


def _project_root() -> Path:
    return projects.resolve_project_root()


def _fmt_uptime(seconds: float | None) -> str:
    if not seconds or seconds < 0:
        return "-"
    s = int(seconds)
    if s < 60:
        return f"{s}s"
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    h, rem = divmod(s, 3600)
    return f"{h}h{rem // 60:02d}m"


def _fmt_idle(last_activity_at: float | None) -> str:
    if not last_activity_at:
        return "-"
    delta = time.time() - last_activity_at
    return _fmt_uptime(delta)


def _exit_with(msg: str, code: int = 1) -> None:
    click.echo(msg, err=True)
    sys.exit(code)


def _cc_override(cc_flag: bool | None) -> str | None:
    """Map Click tristate flag (--cc / --no-cc / unset) to attach mode override."""
    if cc_flag is True:
        return "cc"
    if cc_flag is False:
        return "plain"
    return None


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
)
@click.version_option(version=__version__, prog_name="clad")
def cli() -> None:
    """clad — tmux + Claude Code channels CLI.

    Default form: ``clad "<prompt>" [-t TAG]`` sends a prompt to the
    (project, tag) Claude pane and streams output. Use sub-commands for
    management.
    """


# ----- default prompt path ----- #


@cli.command("prompt")
@click.argument("prompt_text")
@click.option("-t", "--tag", default="default", help="Session tag (per-project).")
@click.option("--detach", is_flag=True,
              help="Send and return immediately; do not stream.")
@click.option("--keepalive", is_flag=True,
              help="Exempt this session from idle auto-close.")
def cmd_prompt(prompt_text: str, tag: str, detach: bool, keepalive: bool) -> None:
    """Send PROMPT_TEXT to the (project, tag) Claude pane.

    To inspect the live pane after sending, run ``clad attach <tag>`` from a
    separate shell.
    """
    project = _project_root()

    try:
        bridge_client.ensure_bridge_running()
        meta = bridge_client.create_session(
            project=str(project), tag=tag, keepalive=keepalive,
            workdir=str(project),
        )
        key = meta["key"]
        ack = bridge_client.post_prompt(key, prompt_text)
        last_event_id = int(ack.get("event_id", 0) or 0)
    except BridgeError as e:
        _exit_with(f"clad: bridge error: {e}", 1)
        return  # unreachable

    if detach:
        click.echo(f"[detached] sent to {tag} ({key})")
        return

    try:
        for event in bridge_client.stream(key, last_event_id=last_event_id):
            _render_event(event)
    except BridgeError as e:
        _exit_with(f"clad: stream error: {e}", 1)
    except KeyboardInterrupt:
        click.echo(
            f"\n[Ctrl+C — session left running. Use `clad close {tag}` to stop.]",
            err=True,
        )
        sys.exit(130)


def _render_event(event: dict) -> None:
    et = event.get("type")
    data = event.get("data")
    if et == "token":
        # Bridge publishes token payloads as plain strings (server.py).
        click.echo(data if isinstance(data, str) else str(data), nl=False)
        sys.stdout.flush()
    elif et == "done":
        click.echo("")
    elif et == "error":
        msg = data.get("message", str(data)) if isinstance(data, dict) else str(data)
        click.echo(f"\n[error] {msg}", err=True)
    elif et == "auto_closed":
        reason = data.get("reason", "") if isinstance(data, dict) else str(data)
        click.echo(f"\n[session auto-closed: {reason}]", err=True)


# ----- management sub-commands ----- #


@cli.command("list")
@click.option("--all", "show_all", is_flag=True, help="Across all projects.")
def cmd_list(show_all: bool) -> None:
    """List active sessions for the current project (or --all)."""
    project = _project_root()
    try:
        bridge_client.ensure_bridge_running()
        sessions = bridge_client.list_sessions(
            all_projects=show_all,
            project=None if show_all else str(project),
        )
    except BridgeError as e:
        _exit_with(f"clad: bridge error: {e}", 1)
        return

    if not sessions:
        click.echo("(no active sessions)")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("TAG")
    table.add_column("PROJECT")
    table.add_column("PANE")
    table.add_column("UPTIME")
    table.add_column("IDLE")
    table.add_column("KA")
    table.add_column("LAST_PROMPT")

    now = time.time()
    for s in sessions:
        uptime = (now - s["created_at"]) if s.get("created_at") else None
        last_prompt = (s.get("last_prompt") or "")[:40]
        if len(s.get("last_prompt") or "") > 40:
            last_prompt += "…"
        ka_mark = "★" if s.get("keepalive") else ""
        proj = s.get("project") or ""
        if len(proj) > 30:
            proj = "…" + proj[-29:]
        table.add_row(
            s["tag"],
            proj,
            s.get("pane_id", ""),
            _fmt_uptime(uptime),
            _fmt_idle(s.get("last_activity_at")),
            ka_mark,
            last_prompt,
        )
    console.print(table)


@cli.command("close")
@click.argument("tag", required=False)
@click.option("--all", "close_all", is_flag=True,
              help="Close all sessions in the current project.")
def cmd_close(tag: str | None, close_all: bool) -> None:
    """Close one TAG (or --all) in the current project."""
    if not tag and not close_all:
        _exit_with("usage: clad close <tag>   or   clad close --all", 2)
    project = _project_root()
    try:
        bridge_client.ensure_bridge_running()
        if close_all:
            sessions = bridge_client.list_sessions(all_projects=False, project=str(project))
            for s in sessions:
                bridge_client.delete_session(s["key"], reason="user")
                click.echo(f"closed {s['tag']}")
        else:
            key = projects.session_key(project, tag)  # type: ignore[arg-type]
            bridge_client.delete_session(key, reason="user")
            click.echo(f"closed {tag}")
    except BridgeError as e:
        _exit_with(f"clad: bridge error: {e}", 1)


@cli.command("attach")
@click.argument("tag")
@click.option("--cc/--no-cc", "cc_flag", default=None)
def cmd_attach(tag: str, cc_flag: bool | None) -> None:
    """Attach tmux to the pane for TAG (auto-detect -CC)."""
    project = _project_root()
    key = projects.session_key(project, tag)
    cfg = config.load()
    try:
        bridge_client.ensure_bridge_running()
        sess = bridge_client.get_session(key)
    except BridgeError as e:
        _exit_with(f"clad: bridge error: {e}", 1)
        return
    if not sess:
        _exit_with(f"clad: no session for tag {tag!r} in this project", 1)
        return
    mode = terminal.resolve_attach_mode(_cc_override(cc_flag), cfg.tmux_attach_mode)
    argv = terminal.build_attach_argv(mode, sess["tmux_session"], sess["pane_id"])
    os.execvp(argv[0], argv)


@cli.command("logs")
@click.argument("tag")
@click.option("--tail", "tail_n", default=200, show_default=True, type=int)
def cmd_logs(tag: str, tail_n: int) -> None:
    """Show captured channel history for TAG."""
    project = _project_root()
    key = projects.session_key(project, tag)
    log_path = paths.session_log(key)
    if not log_path.exists():
        click.echo("(no log yet)")
        return
    with log_path.open("r", encoding="utf-8") as fh:
        lines = fh.readlines()
    for line in lines[-tail_n:]:
        click.echo(line.rstrip())


@cli.command("doctor")
@click.option("--prune", is_flag=True, help="Remove stale state entries.")
def cmd_doctor(prune: bool) -> None:
    """Diagnose tmux/claude/bridge install + report terminal attach mode."""
    from shutil import which

    ok = True

    def check(name: str, cond: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "✔" if cond else "✘"
        click.echo(f"  {mark} {name}" + (f"  {detail}" if detail else ""))
        if not cond:
            ok = False

    click.echo("clad doctor")

    tmux_path = which("tmux")
    check("tmux on PATH", bool(tmux_path), tmux_path or "MISSING")

    claude_path = which("claude")
    check("claude on PATH", bool(claude_path), claude_path or "MISSING")

    try:
        pid, port = bridge_client.ensure_bridge_running()
        check("bridge running", True, f"pid={pid} port={port}")
    except BridgeError as e:
        check("bridge running", False, str(e))

    paths.ensure_layout()
    check("state dir", paths.state_dir().exists(), str(paths.state_dir()))
    cfg = config.load()

    term = terminal.detected_terminal()
    cc_supported = terminal.terminal_supports_control_mode()
    mode = terminal.resolve_attach_mode(None, cfg.tmux_attach_mode)
    extra = " (control mode — new window)" if mode == "cc" else ""
    click.echo(
        f"  ℹ terminal={term}, "
        f"control-mode={'available' if cc_supported else 'unavailable'}, "
        f"attach-mode={mode}{extra}"
    )
    click.echo(
        f"  ℹ idle_timeout_minutes={cfg.idle_timeout_minutes}, "
        f"idle_check_interval_seconds={cfg.idle_check_interval_seconds}"
    )

    if prune:
        from . import state as state_mod
        try:
            from . import tmux as tmux_mod
        except Exception:
            tmux_mod = None
        removed = 0
        with state_mod.transaction() as st:
            for k in list(st.sessions.keys()):
                rec = st.sessions[k]
                alive = False
                if tmux_mod is not None:
                    try:
                        alive = tmux_mod.pane_exists(rec.pane_id)
                    except Exception:
                        alive = False
                if not alive:
                    del st.sessions[k]
                    removed += 1
        click.echo(f"  ↳ pruned {removed} stale session(s)")

    sys.exit(0 if ok else 1)


@cli.group("config")
def cmd_config() -> None:
    """Read/write ~/.clad/config.yaml."""


@cmd_config.command("get")
@click.argument("key")
def cmd_config_get(key: str) -> None:
    try:
        click.echo(config.get(key))
    except KeyError:
        _exit_with(
            f"clad config: unknown key {key!r}. Known: {', '.join(config.all_keys())}",
            2,
        )


@cmd_config.command("set")
@click.argument("key")
@click.argument("value")
def cmd_config_set(key: str, value: str) -> None:
    try:
        new = config.set_value(key, value)
        click.echo(f"{key} = {new}")
    except KeyError:
        _exit_with(
            f"clad config: unknown key {key!r}. Known: {', '.join(config.all_keys())}",
            2,
        )
    except ValueError as e:
        _exit_with(f"clad config: {e}", 2)


@cmd_config.command("list")
def cmd_config_list() -> None:
    cfg = config.load()
    for k in config.all_keys():
        click.echo(f"{k} = {getattr(cfg, k)}")


def _inject_prompt_prefix(argv: list[str]) -> list[str]:
    """If the first arg is a prompt (not a sub-command or group flag), prepend
    'prompt' so Click dispatches to ``cmd_prompt``."""
    if not argv:
        return argv
    first = argv[0]
    if first in _SUBCOMMANDS or first in _GROUP_FLAGS:
        return argv
    if first.startswith("-"):
        # A bare flag without a subcommand — let Click error helpfully
        return argv
    return ["prompt"] + argv


def main() -> None:
    argv = _inject_prompt_prefix(list(sys.argv[1:]))
    cli.main(args=argv, standalone_mode=True)


if __name__ == "__main__":  # pragma: no cover
    main()
