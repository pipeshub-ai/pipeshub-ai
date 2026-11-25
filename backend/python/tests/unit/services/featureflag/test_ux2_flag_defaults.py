"""UX2 FD-1, FD-2: every Python reader of a Labs flag takes its default from ``PLATFORM_FLAG_DEFAULTS``, the one
table checked against Node's registry. Flipping a value in the table is what a reader then returns for an absent
flag, so no reader can carry a literal default of its own."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.services.featureflag import platform_settings
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import (
    PLATFORM_FLAG_DEFAULTS,
    platform_flag_default,
    read_platform_feature_flag,
)

NOTHING_STORED = {"featureFlagsVersion": 2, "featureFlags": {}}


class _Config:
    def __init__(self, settings: object) -> None:
        self.settings = settings

    async def get_config(self, key: str, default: object = None, use_cache: bool = True) -> object:
        return self.settings


class _Unreadable:
    async def get_config(self, key: str, default: object = None, use_cache: bool = True) -> object:
        raise ConnectionError("kv down")


@pytest.fixture
def flipped(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """The table with one flag's default inverted: a reader that follows the table follows it."""

    def flip(flag: str) -> bool:
        value = not PLATFORM_FLAG_DEFAULTS[flag]
        monkeypatch.setitem(PLATFORM_FLAG_DEFAULTS, flag, value)
        return value

    return flip


def test_fd1_a_flag_outside_the_table_is_refused_not_guessed() -> None:
    with pytest.raises(KeyError):
        platform_flag_default("ENABLE_SOMETHING_NOBODY_REGISTERED")
    assert platform_flag_default(CONFIG.ENABLE_MCP.lower()) is PLATFORM_FLAG_DEFAULTS[CONFIG.ENABLE_MCP]


@pytest.mark.parametrize("settings", [NOTHING_STORED, {}, None, {"featureFlags": "garbage"}])
async def test_fd1_absent_or_unreadable_settings_read_the_tables_default(flipped, settings: object) -> None:
    value = flipped(CONFIG.ENABLE_MCP)
    assert await read_platform_feature_flag(CONFIG.ENABLE_MCP, _Config(settings)) is value
    assert await read_platform_feature_flag(CONFIG.ENABLE_MCP, _Unreadable()) is value


async def test_fd2_mcp_reads_the_table(flipped, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.mcp import service

    value = flipped(CONFIG.ENABLE_MCP)
    assert await service.is_mcp_enabled(_Config(NOTHING_STORED)) is value  # type: ignore[arg-type]
    provider = MagicMock()
    provider.get_flag_value.return_value = None
    from app.services.featureflag.featureflag import FeatureFlagService

    flags = FeatureFlagService.__new__(FeatureFlagService)
    flags._provider = provider  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(FeatureFlagService, "get_service", classmethod(lambda cls: flags))
    assert await service.is_mcp_enabled(None) is value


async def test_fd2_vector_store_rebuild_reads_the_table(flipped, monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from app.connectors.api import router

    request = MagicMock()
    request.app.container.config_service.return_value = _Config(NOTHING_STORED)
    monkeypatch.setattr(router, "vector_store_rebuild_available", True)
    allowed = flipped(CONFIG.ENABLE_VECTOR_STORE_REBUILD)
    gate = router._require_vector_store_rebuild_enabled  # pyright: ignore[reportPrivateUsage]
    if allowed:
        await gate(request)
    else:
        with pytest.raises(HTTPException):
            await gate(request)


def test_fd2_feature_flag_service_falls_back_to_the_table(flipped) -> None:
    from app.services.featureflag.featureflag import FeatureFlagService

    provider = MagicMock()
    provider.get_flag_value.return_value = None
    flags = FeatureFlagService.__new__(FeatureFlagService)
    flags._provider = provider  # pyright: ignore[reportPrivateUsage]
    value = flipped(CONFIG.ENABLE_BETA_CONNECTORS)
    assert flags.is_feature_enabled(CONFIG.ENABLE_BETA_CONNECTORS) is value
    assert flags.is_feature_enabled("ENABLE_WORKFLOW_BUILDER") is False
    assert platform_settings.PLATFORM_FLAG_DEFAULTS is PLATFORM_FLAG_DEFAULTS
