import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.code_graph.auto_edge_build import EdgeBuildScheduler

# Short enough that the tests do not sleep noticeably, long enough that a
# second schedule() lands before the first deadline expires.
_QUIET = 0.05


@pytest.fixture
def logger() -> MagicMock:
    return MagicMock(spec=logging.Logger)


def _record(connector_name: str = "GitLab") -> dict[str, str]:
    return {
        "_key": "record-1",
        "connectorName": connector_name,
        "connectorId": "connector-1",
        "orgId": "org-1",
        "recordGroupId": "repo-1",
    }


async def _settle(scheduler: EdgeBuildScheduler) -> None:
    await asyncio.gather(*scheduler._waiters.values(), return_exceptions=True)


@pytest.mark.asyncio
async def test_non_code_connector_is_ignored(logger: MagicMock) -> None:
    build = AsyncMock()
    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)

    scheduler.schedule(_record("Slack"))
    await _settle(scheduler)

    build.assert_not_awaited()


@pytest.mark.asyncio
async def test_builds_once_after_the_group_goes_quiet(logger: MagicMock) -> None:
    build = AsyncMock()
    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)

    for _ in range(5):
        scheduler.schedule(_record())
    await _settle(scheduler)

    build.assert_awaited_once_with(
        org_id="org-1",
        connector_id="connector-1",
        record_group_id="repo-1",
    )


@pytest.mark.asyncio
async def test_each_record_pushes_the_deadline_out(logger: MagicMock) -> None:
    """A steady stream of completions must not trigger a build mid-index."""
    build = AsyncMock()
    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)

    for _ in range(4):
        scheduler.schedule(_record())
        await asyncio.sleep(_QUIET * 0.4)
        build.assert_not_awaited()

    await _settle(scheduler)
    build.assert_awaited_once()


@pytest.mark.asyncio
async def test_separate_record_groups_build_independently(logger: MagicMock) -> None:
    build = AsyncMock()
    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)

    first = _record()
    second = _record()
    second["recordGroupId"] = "repo-2"
    scheduler.schedule(first)
    scheduler.schedule(second)
    await _settle(scheduler)

    assert sorted(
        call.kwargs["record_group_id"] for call in build.await_args_list
    ) == ["repo-1", "repo-2"]


@pytest.mark.asyncio
async def test_missing_scope_does_not_build(logger: MagicMock) -> None:
    build = AsyncMock()
    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)

    record = _record()
    record.pop("recordGroupId")
    scheduler.schedule(record)
    await _settle(scheduler)

    build.assert_not_awaited()
    logger.warning.assert_called_once()


@pytest.mark.asyncio
async def test_build_failure_is_logged_not_raised(logger: MagicMock) -> None:
    """The record whose completion scheduled this already indexed fine."""
    build = AsyncMock(side_effect=RuntimeError("boom"))
    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)

    scheduler.schedule(_record())
    await _settle(scheduler)

    build.assert_awaited_once()
    logger.exception.assert_called_once()


@pytest.mark.asyncio
async def test_completion_during_a_build_schedules_another(logger: MagicMock) -> None:
    """Records that land mid-build must not be swallowed by the in-flight one."""
    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def build(**_: object) -> None:
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()

    scheduler = EdgeBuildScheduler(build, logger, quiet_period=_QUIET)
    scheduler.schedule(_record())
    await asyncio.wait_for(started.wait(), timeout=2)

    scheduler.schedule(_record())
    release.set()
    await _settle(scheduler)

    assert calls == 2
