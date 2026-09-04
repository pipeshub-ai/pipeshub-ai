"""Tests for SandboxSettings and EnvSandboxSettingsLoader."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.coding.base import SandboxContext
from app.agent_loop_lib.sandbox.coding.settings import (
    ConfigServiceSandboxSettingsLoader,
    EnvSandboxSettingsLoader,
    GovernorSettings,
    SandboxSettings,
)

_SANDBOX_ENV_KEYS = (
    "SANDBOX_MODE",
    "SANDBOX_DOCKER_IMAGE",
    "SANDBOX_EGRESS_NETWORK",
    "SANDBOX_PIP_INDEX_URL",
    "SANDBOX_NPM_REGISTRY",
    "SANDBOX_ALLOW_NETWORK",
    "E2B_API_KEY",
    "SANDBOX_MAX_TOTAL",
    "SANDBOX_MAX_PER_ORG",
)


@pytest.fixture(autouse=True)
def _clean_sandbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all sandbox-related env vars before each test."""
    for key in _SANDBOX_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


class TestSandboxSettingsDefaults:
    def test_default_settings(self) -> None:
        s = SandboxSettings()
        assert s.backend == "local"
        assert s.allow_network is True
        assert s.max_concurrent_per_request == 5

    def test_governor_settings_defaults(self) -> None:
        g = GovernorSettings()
        assert g.max_total_sandboxes == 50
        assert g.max_per_org == 10


class TestEnvSandboxSettingsLoader:
    async def test_env_loader_defaults_to_local(self) -> None:
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert settings.backend == "local"
        assert settings.backend_options == {}

    async def test_env_loader_docker_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", "DOCKER")
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())

        assert settings.backend == "docker"
        assert "docker" in settings.backend_options
        opts = settings.backend_options["docker"]
        assert "image" in opts
        assert "egress_network" in opts

    async def test_env_loader_network_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SANDBOX_ALLOW_NETWORK", "false")
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert settings.allow_network is False

    async def test_env_loader_custom_limits(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("SANDBOX_MAX_TOTAL", "20")
        monkeypatch.setenv("SANDBOX_MAX_PER_ORG", "5")
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())

        assert settings.governor.max_total_sandboxes == 20
        assert settings.governor.max_per_org == 5


class TestConfigServiceSandboxSettingsLoader:
    async def test_config_service_loader_raises(self) -> None:
        with pytest.raises(NotImplementedError):
            await ConfigServiceSandboxSettingsLoader().load(SandboxContext())


class TestImplicitLocalFallbackIsLoud:
    """`SANDBOX_MODE` unset resolves to `local`, which is `IsolationLevel.HOST`
    — a subprocess on the service host with the host's network and no way to
    take it away. Every shipped compose file sets `SANDBOX_MODE:-docker`, but
    the Helm chart sets it nowhere, so a Helm install lands here silently.

    Whether that default should change is a deployment decision; that it
    should be SILENT is not.
    """

    def _reset_warning_state(self) -> None:
        from app.agent_loop_lib.sandbox.coding import settings as settings_module

        settings_module._warned_about_host_isolation = False

    async def test_absent_mode_warns(self, monkeypatch, caplog) -> None:
        import logging

        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        self._reset_warning_state()

        with caplog.at_level(logging.WARNING):
            settings = await EnvSandboxSettingsLoader().load(SandboxContext())

        assert settings.backend == "local"
        assert "SANDBOX_MODE" in caplog.text
        assert "isolation" in caplog.text.lower()

    async def test_explicitly_choosing_local_does_not_warn(
        self, monkeypatch, caplog,
    ) -> None:
        """An operator who typed `local` has made the call knowingly; nagging
        them every run would train them to ignore the message that matters."""
        import logging

        monkeypatch.setenv("SANDBOX_MODE", "local")
        self._reset_warning_state()

        with caplog.at_level(logging.WARNING):
            await EnvSandboxSettingsLoader().load(SandboxContext())

        assert "SANDBOX_MODE" not in caplog.text

    async def test_isolated_backend_does_not_warn(self, monkeypatch, caplog) -> None:
        import logging

        monkeypatch.setenv("SANDBOX_MODE", "docker")
        self._reset_warning_state()

        with caplog.at_level(logging.WARNING):
            await EnvSandboxSettingsLoader().load(SandboxContext())

        assert "SANDBOX_MODE" not in caplog.text

    async def test_warns_once_not_per_request(self, monkeypatch, caplog) -> None:
        """`load()` runs on every agent build; a per-request warning would
        bury the logs and get filtered out."""
        import logging

        monkeypatch.delenv("SANDBOX_MODE", raising=False)
        self._reset_warning_state()

        with caplog.at_level(logging.WARNING):
            for _ in range(5):
                await EnvSandboxSettingsLoader().load(SandboxContext())

        # Count RECORDS, not substring hits — the message names the env var
        # more than once.
        warnings = [r for r in caplog.records if "SANDBOX_MODE" in r.getMessage()]
        assert len(warnings) == 1, f"warned {len(warnings)} times across 5 loads"


class TestInvalidSandboxModeIsRejected:
    """A typo'd `SANDBOX_MODE` selected `local` silently — the weakest
    isolation, chosen by the most permissive reading of a value the operator
    clearly meant to be something else. Worse than the unset case: there the
    operator knows they configured nothing, here they believe they configured
    container isolation and got a subprocess on the pod.

    The legacy `app/sandbox/manager.py::get_sandbox_mode` at least logged
    "Unknown SANDBOX_MODE=%s, falling back to local"; the settings loader that
    replaced it dropped even that.
    """

    def _reset_warning_state(self) -> None:
        from app.agent_loop_lib.sandbox.coding import settings as settings_module

        settings_module._warned_about_host_isolation = False

    @pytest.mark.parametrize("value", ["docekr", "daytona", "kubernetes", "e2b2", "loc al"])
    async def test_unrecognised_value_raises(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("SANDBOX_MODE", value)
        self._reset_warning_state()

        with pytest.raises(ValueError) as excinfo:
            await EnvSandboxSettingsLoader().load(SandboxContext())

        message = str(excinfo.value)
        assert value in message, "the rejected value should be echoed back"
        # And the operator needs to know what IS accepted.
        for supported in ("local", "docker", "e2b"):
            assert supported in message.lower()

    @pytest.mark.parametrize(
        "value,expected",
        [("docker", "docker"), ("DOCKER", "docker"), ("Docker", "docker"),
         ("e2b", "e2b"), ("E2B", "e2b"),
         ("local", "local"), ("LOCAL", "local"),
         ("  docker  ", "docker")],
    )
    async def test_supported_values_are_accepted(
        self, monkeypatch, value: str, expected: str,
    ) -> None:
        monkeypatch.setenv("SANDBOX_MODE", value)
        self._reset_warning_state()
        settings = await EnvSandboxSettingsLoader().load(SandboxContext())
        assert settings.backend == expected

    @pytest.mark.parametrize("value", ["", "   "])
    async def test_blank_reads_as_unset_not_invalid(
        self, monkeypatch, value: str, caplog,
    ) -> None:
        """Shell and Compose `${VAR:-default}` both treat an empty value as
        unset, so a blank `SANDBOX_MODE=` follows that convention rather than
        erroring — but it still gets the host-isolation warning."""
        import logging

        monkeypatch.setenv("SANDBOX_MODE", value)
        self._reset_warning_state()

        with caplog.at_level(logging.WARNING):
            settings = await EnvSandboxSettingsLoader().load(SandboxContext())

        assert settings.backend == "local"
        assert "SANDBOX_MODE" in caplog.text

    async def test_explicit_local_is_accepted_without_warning(
        self, monkeypatch, caplog,
    ) -> None:
        import logging

        monkeypatch.setenv("SANDBOX_MODE", "local")
        self._reset_warning_state()

        with caplog.at_level(logging.WARNING):
            settings = await EnvSandboxSettingsLoader().load(SandboxContext())

        assert settings.backend == "local"
        assert "SANDBOX_MODE" not in caplog.text
