import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.code_graph import auto_edge_build


@pytest.fixture
def graph_provider() -> MagicMock:
    provider = MagicMock()
    provider.count_nodes_by_filters = AsyncMock(return_value=0)
    provider.get_nodes_by_filters = AsyncMock(return_value=[])
    provider.get_nodes_updated_since = AsyncMock(return_value=[])
    provider.upsert_sync_point = AsyncMock()
    return provider


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


@pytest.mark.asyncio
async def test_non_code_connector_does_not_query_drain_state(
    graph_provider: MagicMock,
    logger: MagicMock,
) -> None:
    with patch.object(
        auto_edge_build,
        "redis_from_config_service",
        new_callable=AsyncMock,
    ) as redis_factory:
        await auto_edge_build.maybe_trigger_edge_build(
            graph_provider,
            MagicMock(),
            _record("Slack"),
            logger,
        )

    graph_provider.count_nodes_by_filters.assert_not_awaited()
    redis_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_unfinished_records_prevent_lock_attempt(
    graph_provider: MagicMock,
    logger: MagicMock,
) -> None:
    graph_provider.count_nodes_by_filters.return_value = 1

    with patch.object(
        auto_edge_build,
        "redis_from_config_service",
        new_callable=AsyncMock,
    ) as redis_factory:
        await auto_edge_build.maybe_trigger_edge_build(
            graph_provider,
            MagicMock(),
            _record(),
            logger,
        )

    graph_provider.count_nodes_by_filters.assert_awaited_once_with(
        collection=CollectionNames.RECORDS.value,
        filters={"orgId": "org-1", "recordGroupId": "repo-1"},
        in_filters={
            "indexingStatus": [
                ProgressStatus.NOT_STARTED.value,
                ProgressStatus.QUEUED.value,
                ProgressStatus.IN_PROGRESS.value,
            ]
        },
    )
    redis_factory.assert_not_awaited()


@pytest.mark.asyncio
async def test_lock_contention_skips_duplicate_build(
    graph_provider: MagicMock,
    logger: MagicMock,
) -> None:
    redis = MagicMock()
    redis.set = AsyncMock(return_value=False)
    redis.aclose = AsyncMock()

    with (
        patch.object(
            auto_edge_build,
            "redis_from_config_service",
            AsyncMock(return_value=redis),
        ),
        patch.object(
            auto_edge_build,
            "_run_edge_build",
            new_callable=AsyncMock,
        ) as run_edge_build,
    ):
        await auto_edge_build.maybe_trigger_edge_build(
            graph_provider,
            MagicMock(),
            _record(),
            logger,
        )

    redis.set.assert_awaited_once()
    redis.aclose.assert_awaited_once()
    run_edge_build.assert_not_awaited()


@pytest.mark.asyncio
async def test_code_connector_starts_background_build(
    graph_provider: MagicMock,
    logger: MagicMock,
) -> None:
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    redis.aclose = AsyncMock()

    with (
        patch.object(
            auto_edge_build,
            "redis_from_config_service",
            AsyncMock(return_value=redis),
        ),
        patch.object(
            auto_edge_build,
            "_run_edge_build",
            new_callable=AsyncMock,
        ) as run_edge_build,
    ):
        await auto_edge_build.maybe_trigger_edge_build(
            graph_provider,
            MagicMock(),
            _record(),
            logger,
        )
        tasks = tuple(auto_edge_build._edge_build_tasks)
        await asyncio.gather(*tasks)

    run_edge_build.assert_awaited_once()


@pytest.mark.asyncio
async def test_build_failure_is_logged_and_releases_lock(
    graph_provider: MagicMock,
    logger: MagicMock,
) -> None:
    redis = MagicMock()
    redis.eval = AsyncMock()
    redis.aclose = AsyncMock()

    with patch.object(
        auto_edge_build,
        "build_code_graph_edges",
        AsyncMock(side_effect=RuntimeError("build failed")),
    ):
        await auto_edge_build._run_edge_build(
            graph_provider=graph_provider,
            redis=redis,
            lock_key="edge-lock",
            lock_token="owner-token",
            org_id="org-1",
            connector_id="connector-1",
            record_group_id="repo-1",
            logger=logger,
        )

    graph_provider.upsert_sync_point.assert_not_awaited()
    logger.exception.assert_called_once()
    redis.eval.assert_awaited_once_with(
        auto_edge_build._RELEASE_IF_OWNER_LUA,
        1,
        "edge-lock",
        "owner-token",
    )
    redis.aclose.assert_awaited_once()
