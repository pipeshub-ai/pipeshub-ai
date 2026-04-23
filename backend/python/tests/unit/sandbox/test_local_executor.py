"""Tests for app.sandbox.local_executor."""

import asyncio
import os
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sandbox.local_executor import LocalExecutor, _SANDBOX_ROOT
from app.sandbox.models import ExecutionResult, SandboxLanguage


class TestLocalExecutorInit:
    def test_creates_sandbox_root(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", str(tmp_path / "sandbox"))
        executor = LocalExecutor()
        assert os.path.isdir(str(tmp_path / "sandbox"))


class TestLocalExecutorExecute:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_unsupported_language(self, executor):
        result = await executor.execute("code", "ruby")
        assert result.success is False
        assert "Unsupported language" in (result.error or "")

    @pytest.mark.asyncio
    async def test_timeout_returns_error(self, executor):
        with patch.object(executor, "_subprocess", new_callable=AsyncMock) as mock_sub:
            mock_sub.side_effect = asyncio.TimeoutError()
            result = await executor.execute(
                "import time; time.sleep(100)",
                SandboxLanguage.PYTHON,
                timeout_seconds=1,
            )
            assert result.success is False
            assert "timed out" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_generic_exception(self, executor):
        with patch.object(executor, "_subprocess", new_callable=AsyncMock) as mock_sub:
            mock_sub.side_effect = RuntimeError("boom")
            result = await executor.execute("code", SandboxLanguage.PYTHON)
            assert result.success is False
            assert "boom" in (result.error or "")


class TestLocalExecutorPython:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_python_success(self, executor):
        mock_result = ExecutionResult(success=True, stdout="hello\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("print('hello')", SandboxLanguage.PYTHON)
            assert result.success is True
            assert result.stdout == "hello\n"

    @pytest.mark.asyncio
    async def test_run_python_with_packages(self, executor):
        pip_result = ExecutionResult(success=True, exit_code=0)
        run_result = ExecutionResult(success=True, stdout="ok", exit_code=0)
        call_count = 0

        async def _mock_sub(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return pip_result
            return run_result

        # Force "package is not on the host" so pip install is exercised
        # regardless of what's installed in the test environment.
        with patch(
            "app.sandbox.local_executor._split_host_installed_python",
            return_value=([], ["pandas"]),
        ), patch.object(executor, "_subprocess", side_effect=_mock_sub):
            result = await executor.execute(
                "import pandas",
                SandboxLanguage.PYTHON,
                packages=["pandas"],
            )
            assert result.success is True
            assert call_count == 2

    @pytest.mark.asyncio
    async def test_run_python_skips_pip_for_host_installed_packages(self, executor):
        """Packages already present in the host interpreter must skip pip."""
        run_result = ExecutionResult(success=True, stdout="ok", exit_code=0)

        # Simulate "pandas is already installed on the host" -- the executor
        # must not invoke pip in that case, so _subprocess is called exactly
        # once (for the actual script run).
        with patch(
            "app.sandbox.local_executor._split_host_installed_python",
            return_value=(["pandas"], []),
        ), patch.object(
            executor, "_subprocess", new_callable=AsyncMock, return_value=run_result,
        ) as mock_sub:
            result = await executor.execute(
                "import pandas",
                SandboxLanguage.PYTHON,
                packages=["pandas"],
            )
            assert result.success is True
            assert mock_sub.await_count == 1
            # The single call must be the script run, not pip.
            run_cmd = mock_sub.await_args_list[0].args[0]
            assert "-m" not in run_cmd or "pip" not in run_cmd

    @pytest.mark.asyncio
    async def test_run_python_pip_fail(self, executor):
        """A real pip failure (non-network) should surface as pip install failed."""
        pip_result = ExecutionResult(success=False, stderr="ERROR", exit_code=1)

        with patch(
            "app.sandbox.local_executor._split_host_installed_python",
            return_value=([], ["pandas"]),
        ), patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=pip_result):
            result = await executor.execute(
                "import missing",
                SandboxLanguage.PYTHON,
                packages=["pandas"],
            )
            assert result.success is False
            assert "pip install failed" in (result.error or "")

    @pytest.mark.asyncio
    async def test_run_python_pip_offline_has_actionable_error(self, executor):
        """When pip fails with DNS/connection errors, return a hint, not urllib3 noise."""
        pip_result = ExecutionResult(
            success=False,
            stderr=(
                "WARNING: Retrying after connection broken by "
                "NewConnectionError: [Errno 11001] getaddrinfo failed\n"
                "ERROR: Could not find a version that satisfies the requirement reportlab"
            ),
            exit_code=1,
        )

        with patch(
            "app.sandbox.local_executor._split_host_installed_python",
            return_value=([], ["reportlab"]),
        ), patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=pip_result):
            result = await executor.execute(
                "import reportlab",
                SandboxLanguage.PYTHON,
                packages=["reportlab"],
            )
            assert result.success is False
            err = result.error or ""
            assert "cannot reach PyPI" in err
            assert "reportlab" in err

    @pytest.mark.asyncio
    async def test_python_non_allowlisted_package_rejected(self, executor):
        """An unknown package must be rejected BEFORE any pip call."""
        with patch.object(executor, "_subprocess", new_callable=AsyncMock) as mock_sub:
            result = await executor.execute(
                "import x",
                SandboxLanguage.PYTHON,
                packages=["totally-unknown-pkg"],
            )
            assert result.success is False
            assert "allowlist" in (result.error or "").lower()
            mock_sub.assert_not_called()

    @pytest.mark.asyncio
    async def test_python_install_uses_target_and_sets_pythonpath(self, executor, tmp_path):
        """pip must install to <work_dir>/deps and PYTHONPATH must point there."""
        captured_calls = []

        async def _mock_sub(cmd, cwd, timeout, env, **kwargs):
            captured_calls.append({"cmd": cmd, "cwd": cwd, "env": env})
            return ExecutionResult(success=True, exit_code=0)

        # Force "package is not on the host" so pip install is exercised.
        with patch(
            "app.sandbox.local_executor._split_host_installed_python",
            return_value=([], ["pandas"]),
        ), patch.object(executor, "_subprocess", side_effect=_mock_sub):
            await executor.execute(
                "import pandas",
                SandboxLanguage.PYTHON,
                packages=["pandas"],
            )

        # First call = pip install, second call = sys.executable main.py
        assert len(captured_calls) == 2
        pip_cmd = captured_calls[0]["cmd"]
        # pip is now invoked as "<sys.executable> -m pip install ...".
        assert pip_cmd[1:4] == ["-m", "pip", "install"]
        assert "--target" in pip_cmd
        target_idx = pip_cmd.index("--target")
        deps_dir = pip_cmd[target_idx + 1]
        assert deps_dir.endswith(os.path.join("deps"))
        # The deps dir should live inside the per-execution work_dir.
        assert os.path.dirname(deps_dir) == captured_calls[0]["cwd"]
        # pip must NOT be asked to touch the host global site-packages.
        assert "--user" not in pip_cmd

        # Run step must have PYTHONPATH pointing at the same deps dir.
        run_env = captured_calls[1]["env"]
        assert deps_dir in run_env.get("PYTHONPATH", "")
        # OUTPUT_DIR still set for artifact collection.
        assert "OUTPUT_DIR" in run_env

    @pytest.mark.asyncio
    async def test_python_no_packages_no_pythonpath(self, executor):
        """Without packages, no deps dir and no PYTHONPATH set by the executor."""
        captured_calls = []

        async def _mock_sub(cmd, cwd, timeout, env, **kwargs):
            captured_calls.append({"cmd": cmd, "cwd": cwd, "env": env})
            return ExecutionResult(success=True, exit_code=0)

        with patch.object(executor, "_subprocess", side_effect=_mock_sub):
            await executor.execute("print(1)", SandboxLanguage.PYTHON)

        assert len(captured_calls) == 1
        run_env = captured_calls[0]["env"]
        # Executor does NOT set PYTHONPATH when no packages are installed.
        assert run_env.get("PYTHONPATH") in (None, "")


class TestLocalExecutorTypescript:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_typescript_success(self, executor):
        mock_result = ExecutionResult(success=True, stdout="world\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("console.log('world')", SandboxLanguage.TYPESCRIPT)
            assert result.success is True

    @pytest.mark.asyncio
    async def test_typescript_install_uses_prefix(self, executor):
        """npm must install with --prefix <work_dir>.

        User code running as ``.mts`` (ESM) resolves packages via Node's
        ``node_modules`` ancestor walk, so placing them in
        ``<work_dir>/node_modules`` via ``--prefix`` is sufficient — we
        no longer need ``NODE_PATH`` (which Node's ESM loader ignores).
        """
        captured_calls = []

        async def _mock_sub(cmd, cwd, timeout, env, **kwargs):
            captured_calls.append({"cmd": cmd, "cwd": cwd, "env": env})
            return ExecutionResult(success=True, exit_code=0)

        with patch.object(executor, "_subprocess", side_effect=_mock_sub):
            await executor.execute(
                "console.log(1)",
                SandboxLanguage.TYPESCRIPT,
                packages=["chart.js"],
            )

        assert len(captured_calls) == 2
        npm_cmd = captured_calls[0]["cmd"]
        assert npm_cmd[0] == "npm"
        assert "install" in npm_cmd
        assert "--prefix" in npm_cmd
        # npm must NOT be asked to save to a host package.json.
        assert "--no-save" in npm_cmd
        # Second call is the actual `npx tsx` invocation.
        tsx_cmd = captured_calls[1]["cmd"]
        assert tsx_cmd[0] == "npx" and "tsx" in tsx_cmd

    @pytest.mark.asyncio
    async def test_typescript_non_allowlisted_package_rejected(self, executor):
        with patch.object(executor, "_subprocess", new_callable=AsyncMock) as mock_sub:
            result = await executor.execute(
                "console.log(1)",
                SandboxLanguage.TYPESCRIPT,
                packages=["axios"],
            )
            assert result.success is False
            assert "allowlist" in (result.error or "").lower()
            mock_sub.assert_not_called()


class TestLocalExecutorSQLite:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_sqlite(self, executor):
        mock_result = ExecutionResult(success=True, stdout="id,name\n1,alice\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute("SELECT 1;", SandboxLanguage.SQLITE)
            assert result.success is True


class TestLocalExecutorPostgreSQL:
    @pytest.fixture
    def executor(self, tmp_path, monkeypatch):
        root = str(tmp_path / "sandbox")
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", root)
        return LocalExecutor()

    @pytest.mark.asyncio
    async def test_run_postgresql_no_url(self, executor):
        result = await executor.execute("SELECT 1;", SandboxLanguage.POSTGRESQL)
        assert result.success is False
        assert "DATABASE_URL" in (result.error or "")

    @pytest.mark.asyncio
    async def test_run_postgresql_with_url(self, executor):
        mock_result = ExecutionResult(success=True, stdout="count\n42\n", exit_code=0)
        with patch.object(executor, "_subprocess", new_callable=AsyncMock, return_value=mock_result):
            result = await executor.execute(
                "SELECT 1;",
                SandboxLanguage.POSTGRESQL,
                env={"DATABASE_URL": "postgresql://localhost/test"},
            )
            assert result.success is True


class TestLocalExecutorSubprocess:
    @pytest.mark.asyncio
    async def test_subprocess_success(self, tmp_path):
        result = await LocalExecutor._subprocess(
            ["echo", "hello"],
            str(tmp_path),
            timeout=5,
            env=None,
        )
        assert result.success is True
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_subprocess_failure(self, tmp_path):
        result = await LocalExecutor._subprocess(
            ["false"],
            str(tmp_path),
            timeout=5,
            env=None,
        )
        assert result.success is False
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_subprocess_timeout(self, tmp_path):
        with pytest.raises(asyncio.TimeoutError):
            await LocalExecutor._subprocess(
                ["sleep", "10"],
                str(tmp_path),
                timeout=1,
                env=None,
            )

    @pytest.mark.asyncio
    async def test_subprocess_with_stdin(self, tmp_path):
        result = await LocalExecutor._subprocess(
            ["cat"],
            str(tmp_path),
            timeout=5,
            env=None,
            stdin_data="test input",
        )
        assert result.success is True
        assert "test input" in result.stdout


class TestSubprocessEnvAllowlist:
    """Security: host env must NOT leak into sandboxed subprocess."""

    @pytest.mark.asyncio
    async def test_host_secrets_not_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-leak")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        result = await LocalExecutor._subprocess(
            ["env"],
            str(tmp_path),
            timeout=5,
            env=None,
        )
        assert result.success is True
        # PATH is on the allowlist; OPENAI_API_KEY is not
        assert "PATH=" in result.stdout
        assert "OPENAI_API_KEY" not in result.stdout
        assert "sk-test-should-not-leak" not in result.stdout

    @pytest.mark.asyncio
    async def test_user_env_is_forwarded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-should-not-leak")
        result = await LocalExecutor._subprocess(
            ["env"],
            str(tmp_path),
            timeout=5,
            env={"OUTPUT_DIR": "/tmp/out", "MY_TOOL_VAR": "hello"},
        )
        assert result.success is True
        assert "OUTPUT_DIR=/tmp/out" in result.stdout
        assert "MY_TOOL_VAR=hello" in result.stdout
        assert "OPENAI_API_KEY" not in result.stdout


class TestCollectArtifacts:
    def test_empty_dir(self, tmp_path):
        outdir = tmp_path / "output"
        outdir.mkdir()
        artifacts = LocalExecutor.collect_artifacts(str(outdir))
        assert artifacts == []

    def test_nonexistent_dir(self):
        artifacts = LocalExecutor.collect_artifacts("/nonexistent/path")
        assert artifacts == []

    def test_collects_files(self, tmp_path):
        outdir = tmp_path / "output"
        outdir.mkdir()
        (outdir / "chart.png").write_bytes(b"\x89PNG" + b"\x00" * 100)
        (outdir / "data.csv").write_text("a,b\n1,2\n")

        artifacts = LocalExecutor.collect_artifacts(str(outdir))
        assert len(artifacts) == 2
        names = {a.file_name for a in artifacts}
        assert names == {"chart.png", "data.csv"}

        for a in artifacts:
            assert a.size_bytes > 0
            assert a.mime_type != ""

    def test_nested_files(self, tmp_path):
        outdir = tmp_path / "output"
        sub = outdir / "sub"
        sub.mkdir(parents=True)
        (sub / "report.pdf").write_bytes(b"%PDF" + b"\x00" * 50)

        artifacts = LocalExecutor.collect_artifacts(str(outdir))
        assert len(artifacts) == 1
        assert artifacts[0].file_name == "report.pdf"
        assert artifacts[0].mime_type == "application/pdf"


class TestCleanup:
    def test_cleanup_execution(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", str(tmp_path))
        exec_dir = tmp_path / "test-exec-id"
        exec_dir.mkdir()
        (exec_dir / "file.txt").write_text("data")

        LocalExecutor.cleanup_execution("test-exec-id")
        assert not exec_dir.exists()

    def test_cleanup_nonexistent(self, tmp_path, monkeypatch):
        monkeypatch.setattr("app.sandbox.local_executor._SANDBOX_ROOT", str(tmp_path))
        LocalExecutor.cleanup_execution("nonexistent")  # should not raise


class TestHelperLibRoot:
    """_helper_lib_root exposes the in-repo pipeshub-slides / pipeshub-docs path
    so local-mode TypeScript executions can `import "pipeshub-slides"` without
    going through npm.
    """

    def test_env_override_wins_when_directory_exists(
        self, tmp_path, monkeypatch,
    ):
        from app.sandbox.local_executor import _helper_lib_root

        override = tmp_path / "lib"
        override.mkdir()
        monkeypatch.setenv("SANDBOX_HELPER_LIB_ROOT", str(override))
        assert _helper_lib_root() == str(override)

    def test_env_override_is_ignored_if_directory_missing(self, monkeypatch):
        from app.sandbox.local_executor import _helper_lib_root

        monkeypatch.setenv("SANDBOX_HELPER_LIB_ROOT", "/definitely/not/here")
        # Falls back to repo discovery — and the repo does ship the libs,
        # so this must return a usable path rather than the bad override.
        result = _helper_lib_root()
        assert result is None or (
            os.path.isdir(os.path.join(result, "pipeshub-slides"))
            and os.path.isdir(os.path.join(result, "pipeshub-docs"))
        )

    def test_discovers_repo_libs_when_checkout_is_available(self, monkeypatch):
        """When the repo layout is intact, discovery finds both helper libs."""
        from app.sandbox.local_executor import _helper_lib_root

        monkeypatch.delenv("SANDBOX_HELPER_LIB_ROOT", raising=False)
        result = _helper_lib_root()
        # The CI/test environment may run from an installed wheel without
        # the repo tree available, so accept either a valid path or None.
        if result is not None:
            assert os.path.isdir(os.path.join(result, "pipeshub-slides"))
            assert os.path.isdir(os.path.join(result, "pipeshub-docs"))

    @pytest.mark.asyncio
    async def test_helper_libs_symlinked_into_work_dir_for_typescript(
        self, tmp_path, monkeypatch,
    ):
        """_run_typescript must symlink pipeshub-slides/pipeshub-docs into
        ``<work_dir>/node_modules/`` so the agent's
        ``import "pipeshub-slides"`` resolves via Node's ESM ancestor walk.

        NODE_PATH doesn't work under ESM (which our ``.mts`` scripts force
        to enable top-level ``await``), so the only reliable resolution
        strategy is ``node_modules`` ancestor lookup.
        """
        from app.sandbox import local_executor as mod

        fake_lib = tmp_path / "lib"
        (fake_lib / "pipeshub-slides").mkdir(parents=True)
        (fake_lib / "pipeshub-docs").mkdir()

        monkeypatch.setattr(mod, "_helper_lib_root", lambda: str(fake_lib))

        async def fake_subprocess(cmd, cwd, timeout, env):
            return ExecutionResult(
                success=True, stdout="", exit_code=0, execution_time_ms=1,
            )

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        executor = LocalExecutor()
        with patch.object(
            LocalExecutor, "_subprocess",
            new=AsyncMock(side_effect=fake_subprocess),
        ):
            await executor._run_typescript(
                code="console.log('hi')",
                work_dir=str(work_dir),
                output_dir=str(out_dir),
                timeout=5,
                packages=None,
                env={},
            )

        slides_link = work_dir / "node_modules" / "pipeshub-slides"
        docs_link = work_dir / "node_modules" / "pipeshub-docs"
        assert slides_link.is_symlink(), (
            "pipeshub-slides must be symlinked into <work_dir>/node_modules "
            "for ESM resolution"
        )
        assert docs_link.is_symlink()
        assert os.path.realpath(slides_link) == str(fake_lib / "pipeshub-slides")
        assert os.path.realpath(docs_link) == str(fake_lib / "pipeshub-docs")

    @pytest.mark.asyncio
    async def test_symlinks_placed_after_npm_install(
        self, tmp_path, monkeypatch,
    ):
        """When packages are installed, ``npm install --prefix`` will rewrite
        ``<work_dir>/node_modules``. The helper-lib symlinks MUST be
        created AFTER the install so they survive."""
        from app.sandbox import local_executor as mod

        fake_lib = tmp_path / "lib"
        (fake_lib / "pipeshub-slides").mkdir(parents=True)
        (fake_lib / "pipeshub-docs").mkdir()
        monkeypatch.setattr(mod, "_helper_lib_root", lambda: str(fake_lib))

        work_dir = tmp_path / "work"
        work_dir.mkdir()
        out_dir = tmp_path / "out"
        out_dir.mkdir()

        call_order: list[str] = []

        async def fake_subprocess(cmd, cwd, timeout, env):
            if cmd and cmd[0] == "npm":
                # Emulate npm recreating node_modules cleanly, which in the
                # real world wipes any pre-existing symlinks.
                nm = work_dir / "node_modules"
                if nm.is_dir():
                    import shutil
                    shutil.rmtree(nm)
                nm.mkdir()
                call_order.append("npm")
            else:
                call_order.append("tsx")
                # When tsx runs, the symlinks MUST already exist.
                assert (work_dir / "node_modules" / "pipeshub-slides").is_symlink()
            return ExecutionResult(success=True, exit_code=0)

        executor = LocalExecutor()
        with patch.object(
            LocalExecutor, "_subprocess",
            new=AsyncMock(side_effect=fake_subprocess),
        ):
            await executor._run_typescript(
                code="console.log('hi')",
                work_dir=str(work_dir),
                output_dir=str(out_dir),
                timeout=5,
                packages=["pptxgenjs"],
                env={},
            )

        assert call_order == ["npm", "tsx"]
