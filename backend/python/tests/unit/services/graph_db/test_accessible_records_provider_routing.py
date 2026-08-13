"""`get_accessible_virtual_record_ids` with the cache wired in.

The cache must not change *what* the method returns — only how often the graph
is asked. These tests pin the four-scenario branching, the first-seen-wins merge
order, the metadata-filter bypass, and the permission-model routing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors, PermissionModel
from app.services.cache.accessible_records_cache import AccessibleRecordsCache
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

ORG = "org-1"
USER = "user-1"


class RecordingCache(AccessibleRecordsCache):
    """Real cache semantics over a dict, with a log of which entry class was used."""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__(MagicMock(), None, ttl_seconds=300, enabled=False)
        self._enabled = enabled
        self.store: dict[str, dict] = {}
        self.routes: list[tuple[str, str]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def get_or_compute_kb(self, org_id, kb_id, loader):
        self.routes.append(("kb", kb_id))
        key = f"kb:{org_id}:{kb_id}"
        if key not in self.store:
            self.store[key] = await loader()
        return self.store[key]

    async def get_or_compute_app_connector(self, org_id, connector_id, loader):
        self.routes.append(("capp", connector_id))
        key = f"capp:{org_id}:{connector_id}"
        if key not in self.store:
            self.store[key] = await loader()
        return self.store[key]

    async def get_or_compute_user_connector(self, org_id, connector_id, user_id, loader):
        self.routes.append(("cusr", connector_id))
        key = f"cusr:{org_id}:{connector_id}:{user_id}"
        if key not in self.store:
            self.store[key] = await loader()
        return self.store[key]


def _provider(cache=None, apps=None) -> Neo4jProvider:
    provider = Neo4jProvider(MagicMock(), MagicMock(), accessible_records_cache=cache)
    provider.client = MagicMock()
    provider.get_user_by_user_id = AsyncMock(return_value={"id": "user-key-1"})
    provider.get_user_apps = AsyncMock(return_value=apps if apps is not None else [])

    # Every underlying query is stubbed; tests assert on which ones ran.
    provider._get_virtual_ids_for_connector = AsyncMock(return_value={})
    provider._get_kb_virtual_ids = AsyncMock(return_value={})
    provider._get_all_virtual_ids_for_connector = AsyncMock(return_value={})
    provider._get_kb_virtual_ids_for_kb = AsyncMock(return_value={})
    provider._get_accessible_kb_ids = AsyncMock(return_value=[])
    return provider


def _app(app_id: str, app_type: str, permission_model: str | None = None) -> dict:
    doc = {"id": app_id, "type": app_type}
    if permission_model is not None:
        doc["permissionModel"] = permission_model
    return doc


APP_LEVEL_CONNECTOR = _app("conn-app", "S3", PermissionModel.APP_LEVEL.value)
RECORD_LEVEL_CONNECTOR = _app("conn-rec", "DRIVE", PermissionModel.RECORD_LEVEL.value)
KB_APP = _app("kb-1", Connectors.KNOWLEDGE_BASE.value)


class TestCacheDisabledParity:
    """With no cache (connectors/indexing services) nothing may change."""

    async def test_no_cache_uses_the_live_queries(self) -> None:
        provider = _provider(cache=None, apps=[APP_LEVEL_CONNECTOR, KB_APP])

        await provider.get_accessible_virtual_record_ids(USER, ORG)

        provider._get_virtual_ids_for_connector.assert_awaited_once()
        provider._get_kb_virtual_ids.assert_awaited_once()
        provider._get_all_virtual_ids_for_connector.assert_not_called()
        provider._get_kb_virtual_ids_for_kb.assert_not_called()

    async def test_disabled_cache_uses_the_live_queries(self) -> None:
        provider = _provider(cache=RecordingCache(enabled=False), apps=[APP_LEVEL_CONNECTOR, KB_APP])

        await provider.get_accessible_virtual_record_ids(USER, ORG)

        provider._get_virtual_ids_for_connector.assert_awaited_once()
        provider._get_kb_virtual_ids.assert_awaited_once()


class TestPermissionModelRouting:
    async def test_app_level_connector_uses_the_shared_entry(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR])

        await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert cache.routes == [("capp", "conn-app")]
        provider._get_all_virtual_ids_for_connector.assert_awaited_once_with("conn-app")
        provider._get_virtual_ids_for_connector.assert_not_called()

    async def test_record_level_connector_uses_the_per_user_entry(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[RECORD_LEVEL_CONNECTOR])

        await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert cache.routes == [("cusr", "conn-rec")]
        provider._get_virtual_ids_for_connector.assert_awaited_once_with(
            USER, ORG, "conn-rec", None
        )
        provider._get_all_virtual_ids_for_connector.assert_not_called()

    async def test_missing_flag_defaults_to_per_user(self) -> None:
        """An instance predating the flag must not be shared across users."""
        cache = RecordingCache()
        provider = _provider(cache, apps=[_app("conn-old", "SLACK")])

        await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert cache.routes == [("cusr", "conn-old")]

    async def test_two_users_share_an_app_level_entry(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR])
        provider._get_all_virtual_ids_for_connector = AsyncMock(return_value={"vr-1": "rec-1"})

        first = await provider.get_accessible_virtual_record_ids("user-a", ORG)
        second = await provider.get_accessible_virtual_record_ids("user-b", ORG)

        assert first == second == {"vr-1": "rec-1"}
        assert provider._get_all_virtual_ids_for_connector.await_count == 1

    async def test_two_users_do_not_share_a_record_level_entry(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[RECORD_LEVEL_CONNECTOR])
        provider._get_virtual_ids_for_connector = AsyncMock(
            side_effect=[{"vr-a": "rec-a"}, {"vr-b": "rec-b"}]
        )

        first = await provider.get_accessible_virtual_record_ids("user-a", ORG)
        second = await provider.get_accessible_virtual_record_ids("user-b", ORG)

        assert first == {"vr-a": "rec-a"}
        assert second == {"vr-b": "rec-b"}


class TestMetadataFilterBypass:
    async def test_metadata_filters_take_the_live_path(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])

        await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"departments": ["eng"]}
        )

        assert cache.routes == []
        provider._get_virtual_ids_for_connector.assert_awaited_once_with(
            USER, ORG, "conn-app", {"departments": ["eng"]}, time_range=None
        )
        provider._get_kb_virtual_ids.assert_awaited_once_with(
            USER, ORG, None, {"departments": ["eng"]}, time_range=None
        )

    async def test_time_range_takes_the_live_path(self) -> None:
        """A time range narrows the result set and is not in the cache key, so
        serving a cached map would return records outside the window."""
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])
        window = {"source_created_after_ms": 1700000000000}

        await provider.get_accessible_virtual_record_ids(USER, ORG, time_range=window)

        assert cache.routes == []
        provider._get_virtual_ids_for_connector.assert_awaited_once_with(
            USER, ORG, "conn-app", {}, time_range=window
        )
        provider._get_kb_virtual_ids.assert_awaited_once_with(
            USER, ORG, None, {}, time_range=window
        )

    async def test_time_range_bypasses_even_with_kb_and_apps_filters(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])
        window = {"source_updated_before_ms": 1800000000000}

        await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"apps": ["conn-app"], "kb": ["kb-1"]}, time_range=window
        )

        assert cache.routes == [], "a time-filtered request must not read cached maps"

    async def test_kb_and_apps_filters_still_use_the_cache(self) -> None:
        """kb/apps select which entries to read; they are not metadata filters."""
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])

        await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"apps": ["conn-app"], "kb": ["kb-1"]}
        )

        assert ("capp", "conn-app") in cache.routes
        assert ("kb", "kb-1") in cache.routes


class TestScenarioBranching:
    async def test_scenario_1_both_filters(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, RECORD_LEVEL_CONNECTOR, KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])

        await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"apps": ["conn-app"], "kb": ["kb-1"]}
        )

        assert cache.routes == [("capp", "conn-app"), ("kb", "kb-1")]

    async def test_scenario_2_kb_filter_only_skips_connectors(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])

        await provider.get_accessible_virtual_record_ids(USER, ORG, filters={"kb": ["kb-1"]})

        assert cache.routes == [("kb", "kb-1")]

    async def test_scenario_3_no_filters_queries_everything(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, RECORD_LEVEL_CONNECTOR, KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])

        await provider.get_accessible_virtual_record_ids(USER, ORG)

        kinds = {kind for kind, _ in cache.routes}
        assert kinds == {"capp", "cusr", "kb"}

    async def test_scenario_4_app_filter_only_skips_kb_entirely(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])

        await provider.get_accessible_virtual_record_ids(USER, ORG, filters={"apps": ["conn-app"]})

        assert cache.routes == [("capp", "conn-app")]
        provider._get_accessible_kb_ids.assert_not_called()

    async def test_filtered_apps_outside_the_users_access_are_dropped(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR])

        await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"apps": ["conn-app", "conn-not-mine"]}
        )

        assert cache.routes == [("capp", "conn-app")]


class TestMergeOrder:
    async def test_connector_wins_over_kb_for_a_shared_vid(self) -> None:
        """Connector tasks are appended before the KB task, and first seen wins."""
        cache = RecordingCache()
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR, KB_APP])
        provider._get_all_virtual_ids_for_connector = AsyncMock(return_value={"vr-1": "from-connector"})
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])
        provider._get_kb_virtual_ids_for_kb = AsyncMock(return_value={"vr-1": "from-kb"})

        out = await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert out == {"vr-1": "from-connector"}

    async def test_earlier_filtered_connector_wins(self) -> None:
        cache = RecordingCache()
        apps = [
            _app("conn-a", "S3", PermissionModel.APP_LEVEL.value),
            _app("conn-b", "S3", PermissionModel.APP_LEVEL.value),
        ]
        provider = _provider(cache, apps=apps)
        provider._get_all_virtual_ids_for_connector = AsyncMock(
            side_effect=lambda cid: {"vr-1": f"rec-from-{cid}"}
        )

        out = await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"apps": ["conn-a", "conn-b"]}
        )

        assert out == {"vr-1": "rec-from-conn-a"}

    async def test_kb_union_is_first_seen_wins_in_target_order(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1", "kb-2"])
        provider._get_kb_virtual_ids_for_kb = AsyncMock(
            side_effect=lambda kb_id: {"vr-1": f"rec-{kb_id}"}
        )

        out = await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert out == {"vr-1": "rec-kb-1"}


class TestKbAccessResolution:
    async def test_kb_filter_is_intersected_with_live_access(self) -> None:
        """A user must not read a KB entry they cannot reach."""
        cache = RecordingCache()
        provider = _provider(cache, apps=[KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])

        await provider.get_accessible_virtual_record_ids(
            USER, ORG, filters={"kb": ["kb-1", "kb-forbidden"]}
        )

        assert cache.routes == [("kb", "kb-1")]

    async def test_no_kb_access_reads_nothing(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=[])

        out = await provider.get_accessible_virtual_record_ids(USER, ORG, filters={"kb": ["kb-1"]})

        assert out == {}
        assert cache.routes == []

    async def test_access_query_failure_falls_back_to_the_live_path(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(side_effect=RuntimeError("bolt down"))
        provider._get_kb_virtual_ids = AsyncMock(return_value={"vr-1": "rec-1"})

        out = await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert out == {"vr-1": "rec-1"}
        provider._get_kb_virtual_ids.assert_awaited_once_with(USER, ORG, None, None)

    async def test_per_kb_failure_falls_back_to_the_live_path(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[KB_APP])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])
        provider._get_kb_virtual_ids_for_kb = AsyncMock(side_effect=RuntimeError("bolt down"))
        provider._get_kb_virtual_ids = AsyncMock(return_value={"vr-live": "rec-live"})

        out = await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert out == {"vr-live": "rec-live"}


class TestConnectorFallback:
    async def test_cache_error_falls_back_to_the_live_connector_query(self) -> None:
        cache = RecordingCache()

        async def boom(*args, **kwargs):
            raise RuntimeError("redis exploded")

        cache.get_or_compute_app_connector = boom
        provider = _provider(cache, apps=[APP_LEVEL_CONNECTOR])
        provider._get_virtual_ids_for_connector = AsyncMock(return_value={"vr-1": "rec-1"})

        out = await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert out == {"vr-1": "rec-1"}
        provider._get_virtual_ids_for_connector.assert_awaited_once_with(
            USER, ORG, "conn-app", None
        )


class TestUnchangedPreconditions:
    async def test_unknown_user_returns_empty(self) -> None:
        provider = _provider(RecordingCache())
        provider.get_user_by_user_id = AsyncMock(return_value=None)

        assert await provider.get_accessible_virtual_record_ids(USER, ORG) == {}

    async def test_user_with_no_apps_still_checks_kb(self) -> None:
        cache = RecordingCache()
        provider = _provider(cache, apps=[])
        provider._get_accessible_kb_ids = AsyncMock(return_value=["kb-1"])
        provider._get_kb_virtual_ids_for_kb = AsyncMock(return_value={"vr-1": "rec-1"})

        out = await provider.get_accessible_virtual_record_ids(USER, ORG)

        assert out == {"vr-1": "rec-1"}
