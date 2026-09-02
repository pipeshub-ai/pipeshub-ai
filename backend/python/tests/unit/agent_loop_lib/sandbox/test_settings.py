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
