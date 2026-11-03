"""`is_artifact_write_enabled`: the Labs flag `ENABLE_ARTIFACT_WRITE`, off by default."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import is_artifact_write_enabled


def _config_service(flags: dict | None = None) -> AsyncMock:
    svc = AsyncMock()
    svc.get_config = AsyncMock(return_value={} if flags is None else {"featureFlags": flags})
    return svc


async def test_off_by_default() -> None:
    assert await is_artifact_write_enabled(_config_service()) is False
    assert await is_artifact_write_enabled(_config_service({"ENABLE_SOFT_DELETE": True})) is False


async def test_the_labs_flag_turns_it_on() -> None:
    assert CONFIG.ENABLE_ARTIFACT_WRITE == "ENABLE_ARTIFACT_WRITE"
    assert await is_artifact_write_enabled(_config_service({"enable_artifact_write": True})) is True


async def test_saved_off_is_off() -> None:
    assert await is_artifact_write_enabled(_config_service({"ENABLE_ARTIFACT_WRITE": False})) is False


async def test_read_failure_and_no_config_service_are_off() -> None:
    failing = AsyncMock()
    failing.get_config = AsyncMock(side_effect=RuntimeError("kv store unavailable"))
    assert await is_artifact_write_enabled(failing) is False
    assert await is_artifact_write_enabled(None) is False


async def test_reads_through_the_config_cache() -> None:
    svc = _config_service({"ENABLE_ARTIFACT_WRITE": True})
    await is_artifact_write_enabled(svc)
    assert svc.get_config.await_args.kwargs["use_cache"] is True
