"""Unit tests for app.modules.code_graph.edge_build_trigger."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.modules.code_graph import edge_build_trigger


def _record(**overrides) -> dict:
    record = {
        "_key": "record-1",
        "connectorName": "GitLab",
        "connectorId": "connector-1",
        "orgId": "org-1",
        "recordGroupId": "repo-1-code-repository",
        "indexingStatus": ProgressStatus.COMPLETED.value,
    }
    record.update(overrides)
    return record


class TestPublishableScope:
    def test_completed_code_record_yields_its_scope(self) -> None:
        assert edge_build_trigger.publishable_scope(_record()) == (
            "org-1",
            "connector-1",
            "repo-1-code-repository",
        )

    @pytest.mark.parametrize(
        "status",
        [
            ProgressStatus.EMPTY.value,
            ProgressStatus.FAILED.value,
            ProgressStatus.AUTO_INDEX_OFF.value,
            ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
        ],
    )
    def test_every_terminal_status_counts_not_just_completed(self, status) -> None:
        """A repo's last file is as likely to end this way as COMPLETED, and
        nobody else is left to notice the group has drained."""
        assert edge_build_trigger.publishable_scope(_record(indexingStatus=status))

    @pytest.mark.parametrize(
        "status",
        [
            ProgressStatus.NOT_STARTED.value,
            ProgressStatus.QUEUED.value,
            ProgressStatus.IN_PROGRESS.value,
        ],
    )
    def test_a_record_still_in_flight_asks_for_nothing(self, status) -> None:
        assert edge_build_trigger.publishable_scope(_record(indexingStatus=status)) is None

    def test_non_code_connector_is_ignored(self) -> None:
        assert edge_build_trigger.publishable_scope(_record(connectorName="Slack")) is None

    def test_connector_name_spelling_does_not_matter(self) -> None:
        """The same connector reaches us as `GitLab`, `GITLAB` and
        `gitlab_personal` depending on where the value was read from."""
        assert edge_build_trigger.publishable_scope(
            _record(connectorName="GITLAB_PERSONAL")
        )

    @pytest.mark.parametrize("field", ["orgId", "connectorId", "recordGroupId"])
    def test_missing_scope_identifier_yields_nothing(self, field) -> None:
        assert edge_build_trigger.publishable_scope(_record(**{field: None})) is None

    def test_missing_status_yields_nothing(self) -> None:
        assert edge_build_trigger.publishable_scope(_record(indexingStatus=None)) is None

    def test_absent_record_yields_nothing(self) -> None:
        assert edge_build_trigger.publishable_scope(None) is None


class TestGroupHasUnfinishedRecords:
    @pytest.mark.asyncio
    async def test_scopes_the_drain_to_the_record_group(self) -> None:
        """Not the connector: a repo connector also syncs issues and merge
        requests, whose trickle would keep the group looking busy for ever."""
        graph_provider = MagicMock()
        graph_provider.has_nodes_by_filters = AsyncMock(return_value=True)

        assert await edge_build_trigger.group_has_unfinished_records(
            graph_provider, "org-1", "repo-1-code-repository"
        )

        kwargs = graph_provider.has_nodes_by_filters.await_args.kwargs
        assert kwargs["collection"] == CollectionNames.RECORDS.value
        assert kwargs["filters"] == {
            "orgId": "org-1",
            "recordGroupId": "repo-1-code-repository",
        }
        assert set(kwargs["in_filters"]["indexingStatus"]) == {
            ProgressStatus.NOT_STARTED.value,
            ProgressStatus.QUEUED.value,
            ProgressStatus.IN_PROGRESS.value,
        }


class TestReadBuildState:
    @pytest.mark.asyncio
    async def test_no_sync_point_means_never_built_and_nothing_owed(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[])

        assert await edge_build_trigger.read_build_state(
            graph_provider, "org-1", "repo-1"
        ) == (None, False, None)

    @pytest.mark.asyncio
    async def test_reads_every_field_off_the_sync_point(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_nodes_by_filters = AsyncMock(return_value=[{
            "lastEdgeBuildAt": 1700.0, "edgeBuildPending": True,
            "edgeBuildRequestedAt": 1800,
        }])

        state = await edge_build_trigger.read_build_state(graph_provider, "org-1", "repo-1")
        assert state == (1700, True, 1800)
        assert state.requested_at == 1800

        kwargs = graph_provider.get_nodes_by_filters.await_args.kwargs
        assert kwargs["filters"]["syncPointKey"] == "repo-1/code-edge-build"

    @pytest.mark.asyncio
    async def test_unusable_timestamp_reads_as_never_built(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_nodes_by_filters = AsyncMock(
            return_value=[{"lastEdgeBuildAt": "not-a-number", "edgeBuildRequestedAt": True}]
        )

        assert await edge_build_trigger.read_build_state(
            graph_provider, "org-1", "repo-1"
        ) == (None, False, None)


class TestClaimPublish:
    @pytest.mark.asyncio
    async def test_first_caller_wins_the_window(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(return_value=True)

        assert await edge_build_trigger.claim_publish(redis, "org-1", "repo-1")

        kwargs = redis.set.await_args.kwargs
        assert kwargs["nx"] is True
        assert kwargs["ex"] > 0

    @pytest.mark.asyncio
    async def test_the_rest_of_the_tail_loses(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(return_value=None)

        assert not await edge_build_trigger.claim_publish(redis, "org-1", "repo-1")


class TestRenewBuildLock:
    @pytest.mark.asyncio
    async def test_extends_the_lease_then_stops_when_ownership_is_lost(self) -> None:
        redis = MagicMock()
        redis.eval = AsyncMock(side_effect=[1, 0])
        log = MagicMock()

        with patch(
            "app.modules.code_graph.edge_build_trigger.asyncio.sleep", AsyncMock()
        ):
            await edge_build_trigger.renew_build_lock_until_cancelled(
                redis, "lock-key", "token-1", log
            )

        assert redis.eval.await_count == 2
        args = redis.eval.await_args_list[0].args
        assert args[0] == edge_build_trigger.REFRESH_LOCK_IF_OWNER_LUA
        assert args[2:] == ("lock-key", "token-1", edge_build_trigger.BUILD_LOCK_TTL_SECONDS)
        log.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_keeps_renewing_after_a_transient_redis_failure(self) -> None:
        redis = MagicMock()
        redis.eval = AsyncMock(side_effect=[RuntimeError("redis down"), 0])
        log = MagicMock()

        with patch(
            "app.modules.code_graph.edge_build_trigger.asyncio.sleep", AsyncMock()
        ):
            await edge_build_trigger.renew_build_lock_until_cancelled(
                redis, "lock-key", "token-1", log
            )

        assert redis.eval.await_count == 2
        log.exception.assert_called_once()


class TestRecordsUpdatedSince:
    @pytest.mark.asyncio
    async def test_returns_only_string_keys_of_touched_records(self) -> None:
        graph_provider = MagicMock()
        graph_provider.get_nodes_updated_since = AsyncMock(
            return_value=[{"_key": "r1"}, {"_key": None}, {"_key": "r2"}]
        )

        assert await edge_build_trigger.records_updated_since(
            graph_provider, "org-1", "repo-1", 1700
        ) == {"r1", "r2"}

        kwargs = graph_provider.get_nodes_updated_since.await_args.kwargs
        assert kwargs["collection"] == CollectionNames.RECORDS.value
        assert kwargs["timestamp_field"] == "updatedAtTimestamp"
        assert kwargs["since"] == 1700
        assert kwargs["filters"] == {"orgId": "org-1", "recordGroupId": "repo-1"}
        assert kwargs["return_fields"] == ["_key"]


class TestBuildLock:
    @pytest.mark.asyncio
    async def test_acquire_takes_the_key_the_handler_uses(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(return_value=True)

        lock = await edge_build_trigger.acquire_build_lock(redis, "org-1", "repo-1")

        assert lock is not None
        lock_key, lock_token = lock
        assert lock_key == f"{edge_build_trigger.BUILD_LOCK_PREFIX}org-1:repo-1"
        assert redis.set.await_args.args == (lock_key, lock_token)
        assert redis.set.await_args.kwargs == {
            "nx": True,
            "ex": edge_build_trigger.BUILD_LOCK_TTL_SECONDS,
        }

    @pytest.mark.asyncio
    async def test_acquire_yields_to_a_running_build(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(return_value=None)

        assert await edge_build_trigger.acquire_build_lock(redis, "org-1", "repo-1") is None

    @pytest.mark.asyncio
    async def test_release_only_drops_our_own_lease(self) -> None:
        redis = MagicMock()
        redis.eval = AsyncMock(return_value=0)

        assert not await edge_build_trigger.release_build_lock(redis, "lock-key", "token-1")

        assert redis.eval.await_args.args == (
            edge_build_trigger.RELEASE_LOCK_IF_OWNER_LUA, 1, "lock-key", "token-1",
        )


class TestPendingRules:
    BuildState = edge_build_trigger.BuildState

    def test_a_request_after_the_build_started_stays_owed(self) -> None:
        assert edge_build_trigger.still_owed(self.BuildState(1000, True, 2001), 2000)

    def test_a_request_the_build_covered_is_settled(self) -> None:
        assert not edge_build_trigger.still_owed(self.BuildState(1000, True, 1999), 2000)
        assert not edge_build_trigger.still_owed(self.BuildState(1000, True, None), 2000)

    def test_a_fresh_pending_request_is_not_stale(self) -> None:
        """The tail of a repo: the first record marked it pending seconds ago."""
        assert not edge_build_trigger.request_is_stale(self.BuildState(None, True, 100_000), 110_000)

    def test_an_old_pending_request_is_presumed_lost(self) -> None:
        assert edge_build_trigger.request_is_stale(self.BuildState(None, True, 100_000), 161_000)

    def test_a_pending_mark_with_no_timestamp_is_presumed_lost(self) -> None:
        assert edge_build_trigger.request_is_stale(self.BuildState(None, True, None), 0)

    def test_nothing_pending_is_never_stale(self) -> None:
        assert not edge_build_trigger.request_is_stale(self.BuildState(None, False, 0), 10**9)


class TestDeferralClaim:
    @pytest.mark.asyncio
    async def test_claim_outlives_the_delay(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(return_value=True)

        assert await edge_build_trigger.claim_deferral(redis, "org-1", "repo-1")

        kwargs = redis.set.await_args.kwargs
        assert kwargs["nx"] is True
        assert kwargs["ex"] > edge_build_trigger.DEFERRED_BUILD_DELAY_SECONDS

    @pytest.mark.asyncio
    async def test_release_clears_the_same_key(self) -> None:
        redis = MagicMock()
        redis.set = AsyncMock(return_value=True)
        redis.delete = AsyncMock()

        await edge_build_trigger.claim_deferral(redis, "org-1", "repo-1")
        await edge_build_trigger.release_deferral(redis, "org-1", "repo-1")

        assert redis.delete.await_args.args[0] == redis.set.await_args.args[0]
