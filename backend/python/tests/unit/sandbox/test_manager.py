"""Tests for app.sandbox.manager."""

import logging
from collections.abc import Iterator

import pytest

from app.sandbox.manager import (
    SandboxMode,
    SandboxUnavailableError,
    get_executor,
    get_sandbox_mode,
    reset_executor,
)


@pytest.fixture(autouse=True)
def _fresh_warning_state() -> Iterator[None]:
    from app.agent_loop_lib.sandbox.coding import settings as settings_module

    settings_module._warned_about_host_isolation = False
    yield
    settings_module._warned_about_host_isolation = False


class TestGetSandboxMode:
    def test_unset_is_unavailable(self, monkeypatch) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        with pytest.raises(SandboxUnavailableError, match="SANDBOX_MODE is not set"):
            get_sandbox_mode()

    @pytest.mark.parametrize("value", ["", "   "])
    def test_blank_is_unavailable(self, monkeypatch, value) -> None:
        monkeypatch.setenv("SANDBOX_MODE", value)
        with pytest.raises(SandboxUnavailableError):
            get_sandbox_mode()

    def test_local(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        assert get_sandbox_mode() == SandboxMode.LOCAL

    def test_local_without_dev_flag_is_unavailable(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.delenv("SANDBOX_ALLOW_LOCAL", raising=False)
        with pytest.raises(SandboxUnavailableError, match="SANDBOX_ALLOW_LOCAL=true"):
            get_sandbox_mode()

    def test_docker(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        assert get_sandbox_mode() == SandboxMode.DOCKER

    def test_case_insensitive(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "DOCKER")
        assert get_sandbox_mode() == SandboxMode.DOCKER

    @pytest.mark.parametrize("value", ["kubernetes", "docekr", "loc al"])
    def test_unknown_is_unavailable_and_names_accepted_values(
        self, monkeypatch, caplog, value,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", value)
        with caplog.at_level(logging.ERROR), pytest.raises(SandboxUnavailableError) as excinfo:
            get_sandbox_mode()
        assert value in str(excinfo.value)
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1
        for accepted in ("docker", "e2b", "local"):
            assert accepted in errors[0].getMessage()

    def test_e2b_is_valid_elsewhere_but_unavailable_for_this_executor(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "e2b")
        with pytest.raises(SandboxUnavailableError, match="not supported by this executor"):
            get_sandbox_mode()


class TestGetExecutor:
    def setup_method(self) -> None:
        reset_executor()

    def teardown_method(self) -> None:
        reset_executor()

    def test_unset_raises_and_caches_nothing(self, monkeypatch) -> None:
        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        with pytest.raises(SandboxUnavailableError):
            get_executor()
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        from app.sandbox.docker_executor import DockerExecutor
        assert isinstance(get_executor(), DockerExecutor)

    def test_local_without_dev_flag_builds_no_executor(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "false")
        with pytest.raises(SandboxUnavailableError):
            get_executor()
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        from app.sandbox.local_executor import LocalExecutor
        assert isinstance(get_executor(), LocalExecutor)

    def test_garbage_raises(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "kubernetes")
        with pytest.raises(SandboxUnavailableError):
            get_executor()

    def test_explicit_local_returns_local_executor_with_warning(self, monkeypatch, caplog) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "LOCAL")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        with caplog.at_level(logging.WARNING):
            executor = get_executor()
        from app.sandbox.local_executor import LocalExecutor
        assert isinstance(executor, LocalExecutor)
        assert any(
            "subprocess" in r.getMessage() and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_returns_docker_executor(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "docker")
        executor = get_executor()
        from app.sandbox.docker_executor import DockerExecutor
        assert isinstance(executor, DockerExecutor)

    def test_singleton(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        e1 = get_executor()
        e2 = get_executor()
        assert e1 is e2

    def test_reset_clears_singleton(self, monkeypatch) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "local")
        monkeypatch.setenv("SANDBOX_ALLOW_LOCAL", "true")
        e1 = get_executor()
        reset_executor()
        e2 = get_executor()
        assert e1 is not e2
