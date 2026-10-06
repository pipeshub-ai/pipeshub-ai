"""`is_chat_agent_builder_enabled` reads ENABLE_CHAT_AGENT_BUILDER, default off."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.featureflag.platform_settings import is_chat_agent_builder_enabled


def _config_service(flags: dict | None) -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={} if flags is None else {"featureFlags": flags})
    return svc


@pytest.mark.parametrize(
    ("flags", "expected"),
    [(None, False), ({}, False), ({"ENABLE_CHAT_AGENT_BUILDER": False}, False), ({"ENABLE_CHAT_AGENT_BUILDER": True}, True)],
)
async def test_defaults_off(flags: dict | None, expected: bool) -> None:
    assert await is_chat_agent_builder_enabled(_config_service(flags)) is expected


async def test_a_read_failure_is_off() -> None:
    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=RuntimeError("kv store unavailable"))
    assert await is_chat_agent_builder_enabled(svc) is False
