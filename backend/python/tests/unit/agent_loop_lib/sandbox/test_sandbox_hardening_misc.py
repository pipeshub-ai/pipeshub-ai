"""Smaller sandbox hardening fixes, each pinned here.

- Package specs reach the E2B and the legacy Docker install commands as
  single quoted words (a version range is not a shell redirection).
- The legacy executor's dependency archive is bounded in memory.
- Local working dirs are private (0700), local installs time out, and local
  mode without confinement is reported once at ERROR.
- ``entry_file`` must be a plain relative path inside the working dir.
- The sandbox image's Python base is pinned to a patch release with the
  tarfile filter fixes.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import types
from collections.abc import Iterator  # noqa: TC003
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.agent_loop_lib.sandbox.coding import environment as environment_mod
from app.agent_loop_lib.sandbox.coding.base import CodeRequest
from app.agent_loop_lib.sandbox.coding.environment import EnvironmentManager
from app.agent_loop_lib.sandbox.coding.local import LocalCodingSandbox

REPO = Path(__file__).resolve().parents[6]


@pytest.fixture
def fake_e2b_module(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("e2b_code_interpreter")
    mod.CommandExitException = type("CommandExitException", (Exception,), {})
    mod.TimeoutException = type("TimeoutException", (Exception,), {})
    monkeypatch.setitem(sys.modules, "e2b_code_interpreter", mod)


class TestInstallCommandsQuoteSpecs:
    async def test_e2b_quotes_each_spec(self, fake_e2b_module, monkeypatch) -> None:
        import app.agent_loop_lib.sandbox.coding.e2b as e2b_mod
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        # The spec validator refuses these ranges first; quoting is the layer behind it.
        monkeypatch.setattr(e2b_mod, "validate_package_spec", lambda *_a, **_k: True)
        sandbox = E2BCodingSandbox()
        sandbox._sandbox_id = "e2b-1"
        sbx = MagicMock()
        sbx.commands.run = AsyncMock(return_value=MagicMock(stdout="", stderr="", exit_code=0))
        sandbox._sbx = sbx

        result = await sandbox.install_packages(["mpmath<1.0", "numpy>=1.26"], "python")

        assert result.success
        cmd = sbx.commands.run.call_args.args[0]
        assert cmd == "pip install --no-cache-dir --only-binary=:all: 'mpmath<1.0' 'numpy>=1.26'"

    async def test_e2b_output_is_capped(self, fake_e2b_module) -> None:
        from app.agent_loop_lib.sandbox.coding.e2b import E2BCodingSandbox

        sandbox = E2BCodingSandbox()
        sandbox._sandbox_id = "e2b-1"
        sbx = MagicMock()
        sbx.commands.run = AsyncMock(return_value=MagicMock(stdout="y" * (20 * 1024 * 1024), stderr="", exit_code=0))
        sandbox._sbx = sbx

        stdout, _stderr, _code = await sandbox._run_command("python3 main.py", timeout=5)

        assert len(stdout) < 300 * 1024 and "bytes truncated" in stdout

    def test_legacy_docker_executor_quotes_each_spec(self, tmp_path, monkeypatch) -> None:
        fake = types.ModuleType("docker")
        errors_mod = types.ModuleType("docker.errors")
        errors_mod.ImageNotFound = type("ImageNotFound", (Exception,), {})
        fake.errors = errors_mod
        fake.from_env = MagicMock()
        monkeypatch.setitem(sys.modules, "docker", fake)
        monkeypatch.setitem(sys.modules, "docker.errors", errors_mod)
        import app.sandbox.docker_executor as legacy
        from app.sandbox.models import SandboxLanguage

        monkeypatch.setattr(legacy, "_SANDBOX_ROOT", str(tmp_path))
        monkeypatch.setattr(legacy, "ensure_egress_network_sync", lambda *a, **k: ["10.9.0.0/16"])
        executor = legacy.DockerExecutor()
        captured: dict = {}

        def _install_once(*args: object) -> bytes:
            captured["script"] = args[5]["command"][-1]
            return b""

        monkeypatch.setattr(executor, "_install_once", _install_once)
        executor._install_dependencies(["mpmath<1.0", "lodash@4.*"], SandboxLanguage.PYTHON, timeout=60)

        assert "'mpmath<1.0'" in captured["script"]
        assert "'lodash@4.*'" in captured["script"]

    def test_legacy_dependency_archive_is_bounded(self) -> None:
        import app.sandbox.docker_executor as legacy

        produced = {"n": 0}

        def _chunks() -> Iterator[bytes]:
            # Finite, so a reader that stops enforcing the limit fails the
            # assertion below instead of reading forever.
            for _ in range(10):
                produced["n"] += 1
                yield b"\0" * (1024 * 1024)

        container = MagicMock()
        container.get_archive.return_value = (_chunks(), {})
        with pytest.raises(RuntimeError, match="MiB of dependencies"):
            legacy._get_archive_bytes(container, "/deps", limit=8 * 1024 * 1024)
        assert produced["n"] <= 9


class TestLocalMode:
    async def test_working_dir_is_private_whatever_the_umask(self, tmp_path) -> None:
        old = os.umask(0o002)
        try:
            sandbox = LocalCodingSandbox(working_dir=str(tmp_path / "alcs-x"))
            await sandbox.provision()
        finally:
            os.umask(old)
        assert (os.stat(sandbox.working_dir).st_mode & 0o777) == 0o700

    async def test_docker_working_dir_is_private(self, tmp_path) -> None:
        from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

        old = os.umask(0o002)
        try:
            sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "alcs-docker-x"))
            await sandbox.provision()
        finally:
            os.umask(old)
        assert (os.stat(sandbox.working_dir).st_mode & 0o777) == 0o700

    async def test_a_hanging_install_times_out_and_is_killed(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(environment_mod, "_INSTALL_TIMEOUT_S", 0.5, raising=False)
        env = EnvironmentManager(str(tmp_path))
        marker = tmp_path / "still-running"
        script = (
            "import time, pathlib\n"
            "time.sleep(3)\n"
            f"pathlib.Path({str(marker)!r}).write_text('x')\n"
        )
        result = await asyncio.wait_for(
            env._run_confined([sys.executable, "-c", script], allow_network=False), timeout=10,
        )
        assert result.exit_code != 0
        assert "timed out" in result.stderr
        await asyncio.sleep(3.5)
        assert not marker.exists(), "the install's process group must be killed"

    def test_unconfined_local_mode_is_an_error_once(self, monkeypatch, caplog) -> None:
        from app.agent_loop_lib.sandbox import confinement
        from app.agent_loop_lib.sandbox.coding import settings as settings_mod

        monkeypatch.setattr(confinement, "confinement_available", lambda: False)
        monkeypatch.setattr(confinement, "_reported_unconfined", False, raising=False)
        monkeypatch.setattr(settings_mod, "_warned_about_host_isolation", False)
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        with caplog.at_level(logging.ERROR):
            settings_mod.resolve_sandbox_mode()
            settings_mod._warned_about_host_isolation = False
            settings_mod.resolve_sandbox_mode()
        errors = [
            r for r in caplog.records
            if r.levelno == logging.ERROR and "unconfined" in r.getMessage()
        ]
        assert len(errors) == 1
        assert "environment" in errors[0].getMessage()


class TestEntryFile:
    @pytest.mark.parametrize("entry", [
        "../escape.py", "/etc/passwd", "a/../../b.py", "..", ".hidden/../x.py",
        "main.py; rm -rf /", "$(id).py", "a b.py", "-rf.py", "", "sub//x.py", "C:\\x.py",
    ])
    def test_unsafe_entry_files_are_refused(self, entry: str) -> None:
        with pytest.raises(ValidationError):
            CodeRequest(code="", entry_file=entry)

    @pytest.mark.parametrize("entry", ["main.py", "custom_script.py", "src/app.ts", "a-b_c.1.py"])
    def test_plain_relative_paths_are_accepted(self, entry: str) -> None:
        assert CodeRequest(code="", entry_file=entry).entry_file == entry

    async def test_backends_check_it_even_when_the_model_was_bypassed(self, tmp_path) -> None:
        from app.agent_loop_lib.sandbox.coding.docker import DockerCodingSandbox

        request = CodeRequest.model_construct(
            code="", language="python", timeout=5.0, packages=[], allow_network=False,
            entry_file="../../outside.py",
        )
        sandbox = DockerCodingSandbox(working_dir=str(tmp_path / "wd"))
        with patch.object(sandbox, "_ensure_image", AsyncMock()), pytest.raises(ValueError):
            await sandbox.execute(request)
        assert not (tmp_path / "outside.py").exists()


def test_sandbox_image_python_is_pinned_to_a_fixed_patch_release() -> None:
    dockerfile = (REPO / "deployment" / "sandbox" / "Dockerfile").read_text()
    m = re.search(r"^FROM python:(\d+)\.(\d+)\.(\d+)-slim\b", dockerfile, re.M)
    assert m, "the base image must name a patch release, not a floating tag"
    major, minor, patch_ = (int(g) for g in m.groups())
    assert (major, minor) == (3, 12)
    assert patch_ >= 11
