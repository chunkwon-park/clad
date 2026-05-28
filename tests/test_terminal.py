"""Terminal -CC detection and attach argv builder."""
from __future__ import annotations

import pytest

from clad import terminal


@pytest.mark.parametrize(
    "env,expected",
    [
        ({"TERM_PROGRAM": "iTerm.app"}, True),
        ({"TERM_PROGRAM": "WezTerm"}, True),
        ({"LC_TERMINAL": "iTerm2"}, True),
        ({"TERM_PROGRAM": "Apple_Terminal"}, False),
        ({}, False),
        ({"TERM_PROGRAM": "vscode"}, False),
    ],
)
def test_terminal_supports_control_mode(env, expected) -> None:
    assert terminal.terminal_supports_control_mode(env) is expected


@pytest.mark.parametrize(
    "cli_flag,config_value,env,expected",
    [
        ("cc", "auto", {"TERM_PROGRAM": "Apple_Terminal"}, "cc"),
        ("plain", "auto", {"TERM_PROGRAM": "iTerm.app"}, "plain"),
        (None, "cc", {"TERM_PROGRAM": "Apple_Terminal"}, "cc"),
        (None, "plain", {"TERM_PROGRAM": "iTerm.app"}, "plain"),
        (None, "auto", {"TERM_PROGRAM": "iTerm.app"}, "cc"),
        (None, "auto", {"TERM_PROGRAM": "WezTerm"}, "cc"),
        (None, "auto", {"LC_TERMINAL": "iTerm2"}, "cc"),
        (None, "auto", {"TERM_PROGRAM": "Apple_Terminal"}, "plain"),
        (None, "auto", {}, "plain"),
    ],
)
def test_resolve_attach_mode(cli_flag, config_value, env, expected) -> None:
    assert terminal.resolve_attach_mode(cli_flag, config_value, env) == expected


def test_build_attach_argv_cc() -> None:
    argv = terminal.build_attach_argv("cc", "clad-abc", "%12")
    assert argv[:5] == ["tmux", "-CC", "attach-session", "-t", "clad-abc"]
    assert ";" in argv
    assert "select-pane" in argv
    assert "%12" in argv


def test_build_attach_argv_plain() -> None:
    argv = terminal.build_attach_argv("plain", "clad-abc", "%12")
    assert argv[:4] == ["tmux", "attach-session", "-t", "clad-abc"]
    assert ";" in argv


def test_build_attach_argv_no_pane() -> None:
    argv = terminal.build_attach_argv("plain", "clad-abc", "")
    # No select-pane appended when pane_id is empty
    assert ";" not in argv
