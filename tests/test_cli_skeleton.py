"""CLI sub-command surface — verify help works without spinning up the bridge."""
from __future__ import annotations

from click.testing import CliRunner

from clad.cli import cli, _inject_prompt_prefix


def test_top_level_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    out = result.output
    for cmd in ("list", "close", "attach", "logs", "doctor", "config", "prompt"):
        assert cmd in out


def test_each_subcommand_help() -> None:
    runner = CliRunner()
    for cmd in ("prompt", "list", "close", "attach", "logs", "doctor", "config"):
        result = runner.invoke(cli, [cmd, "--help"])
        assert result.exit_code == 0, f"{cmd} --help failed: {result.output}"


def test_inject_prompt_prefix_for_bare_prompt() -> None:
    """A non-subcommand first arg gets a synthetic 'prompt' prepended."""
    assert _inject_prompt_prefix(["some prompt", "-a"]) == ["prompt", "some prompt", "-a"]
    assert _inject_prompt_prefix(["list"]) == ["list"]
    assert _inject_prompt_prefix(["config", "list"]) == ["config", "list"]
    assert _inject_prompt_prefix(["--help"]) == ["--help"]
    assert _inject_prompt_prefix([]) == []


def test_prompt_has_no_attach_flag() -> None:
    """``-a``/``--attach`` was removed from `clad prompt` because iTerm2 -CC
    control mode leaves the CLI in an interactive tmux UI without returning.
    `clad attach <tag>` is the only attach path."""
    runner = CliRunner()
    result = runner.invoke(cli, ["prompt", "x", "-a"])
    assert result.exit_code == 2
    assert "No such option" in result.output or "no such option" in result.output.lower()


def test_config_list(isolated_clad_home) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "list"])
    assert result.exit_code == 0
    assert "idle_timeout_minutes" in result.output


def test_config_get_set(isolated_clad_home) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "idle_timeout_minutes", "7"])
    assert result.exit_code == 0
    result = runner.invoke(cli, ["config", "get", "idle_timeout_minutes"])
    assert result.exit_code == 0
    assert "7" in result.output


def test_config_set_unknown_key(isolated_clad_home) -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["config", "set", "bogus", "1"])
    assert result.exit_code == 2
    assert "unknown key" in result.output
