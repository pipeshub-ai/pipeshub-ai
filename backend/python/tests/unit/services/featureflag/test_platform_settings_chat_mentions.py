"""`is_chat_mentions_enabled` gates `@assistant help` the way Node gates the mention routes and the UI gates
the composer: both ENABLE_COLLABORATIVE_CHATS and ENABLE_CHAT_MENTIONS, default off."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.featureflag.platform_settings import is_chat_mentions_enabled


def _config_service(flags: dict | None) -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={} if flags is None else {"featureFlags": flags})
    return svc


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (None, False),
        ({}, False),
        ({"ENABLE_CHAT_MENTIONS": True}, False),
        ({"ENABLE_COLLABORATIVE_CHATS": True}, False),
        ({"ENABLE_COLLABORATIVE_CHATS": False, "ENABLE_CHAT_MENTIONS": True}, False),
        ({"ENABLE_COLLABORATIVE_CHATS": True, "ENABLE_CHAT_MENTIONS": False}, False),
        ({"ENABLE_COLLABORATIVE_CHATS": True, "ENABLE_CHAT_MENTIONS": True}, True),
    ],
)
async def test_needs_both_flags(flags: dict | None, expected: bool) -> None:
    assert await is_chat_mentions_enabled(_config_service(flags)) is expected


async def test_a_read_failure_is_off() -> None:
    svc = AsyncMock()
    svc.get_config = AsyncMock(side_effect=RuntimeError("kv store unavailable"))
    assert await is_chat_mentions_enabled(svc) is False
