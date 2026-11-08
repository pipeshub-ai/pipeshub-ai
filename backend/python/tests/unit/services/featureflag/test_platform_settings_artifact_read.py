"""`artifact_read_mode`: the Labs flags `ENABLE_ARTIFACT_READ` and `ENABLE_ARTIFACT_READ_SHADOW`, off by default."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import artifact_read_mode


def _config_service(flags: dict | None = None) -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={} if flags is None else {"featureFlags": flags})
    return svc


def test_flag_names() -> None:
    assert CONFIG.ENABLE_ARTIFACT_READ == "ENABLE_ARTIFACT_READ"
    assert CONFIG.ENABLE_ARTIFACT_READ_SHADOW == "ENABLE_ARTIFACT_READ_SHADOW"


@pytest.mark.parametrize(("flags", "mode"), [
    (None, "legacy"),
    ({}, "legacy"),
    ({"ENABLE_ARTIFACT_WRITE": True}, "legacy"),
    ({"ENABLE_ARTIFACT_READ_SHADOW": True}, "shadow"),
    ({"enable_artifact_read": True}, "artifact"),
    ({"ENABLE_ARTIFACT_READ": True, "ENABLE_ARTIFACT_READ_SHADOW": True}, "artifact"),
    ({"ENABLE_ARTIFACT_READ": False, "ENABLE_ARTIFACT_READ_SHADOW": True}, "shadow"),
    ({"ENABLE_ARTIFACT_READ": False, "ENABLE_ARTIFACT_READ_SHADOW": False}, "legacy"),
])
async def test_modes(flags: dict | None, mode: str) -> None:
    assert await artifact_read_mode(_config_service(flags)) == mode


async def test_read_failure_and_no_config_service_read_the_stored_record() -> None:
    failing = AsyncMock()
    failing.get_config = AsyncMock(side_effect=RuntimeError("kv store unavailable"))
    assert await artifact_read_mode(failing) == "legacy"
    assert await artifact_read_mode(None) == "legacy"


async def test_reads_through_the_config_cache() -> None:
    svc = _config_service({"ENABLE_ARTIFACT_READ_SHADOW": True})
    await artifact_read_mode(svc)
    assert all(call.kwargs["use_cache"] is True for call in svc.get_config.await_args_list)
