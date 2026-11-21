"""Without a licence key, a personal connector of each source is limited to a few people."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.api.router import _setup_failure_message
from app.connectors.core.base.data_processor.permission_sync_gate import (
    PERSONAL_CONNECTOR_CAP,
    PersonalConnectorCapReachedError,
    PersonalConnectorCapUnavailableError,
    personal_cap_applies,
    personal_cap_reached,
)
from app.connectors.core.registry.connector_registry import ConnectorRegistry

_REGISTRY = "app.connectors.core.registry.connector_registry"
_GATE = f"{_REGISTRY}.permission_sync_licensed"


def _apps(*owners: str, scope: str = "personal") -> list[dict]:
    return [{"_key": f"app-{i}", "createdBy": o, "scope": scope} for i, o in enumerate(owners)]


def _others(n: int) -> list[dict]:
    return _apps(*(f"user-{i}" for i in range(n)))


class TestPersonalCapReached:
    def test_below_the_cap_a_new_person_may_connect(self) -> None:
        assert not personal_cap_reached("newcomer", _others(PERSONAL_CONNECTOR_CAP - 1))

    def test_at_the_cap_a_new_person_is_refused(self) -> None:
        assert personal_cap_reached("newcomer", _others(PERSONAL_CONNECTOR_CAP))

    def test_over_the_cap_existing_connectors_stay_and_newcomers_are_refused(self) -> None:
        assert personal_cap_reached("newcomer", _others(PERSONAL_CONNECTOR_CAP + 3))

    def test_someone_who_already_has_one_may_add_another(self) -> None:
        apps = _others(PERSONAL_CONNECTOR_CAP + 3) + _apps("user-0")
        assert not personal_cap_reached("user-0", apps)

    def test_people_are_counted_once_however_many_they_have(self) -> None:
        apps = _apps(*["user-0"] * 10, *(f"user-{i}" for i in range(1, PERSONAL_CONNECTOR_CAP - 1)))
        assert not personal_cap_reached("newcomer", apps)

    def test_company_wide_connectors_do_not_count(self) -> None:
        apps = _apps(*(f"admin-{i}" for i in range(10)), scope="team")
        assert not personal_cap_reached("newcomer", apps)


class TestWhichSourcesAreCapped:
    @pytest.mark.parametrize("connector_type", ["Drive", "Gmail", "Slack", "Dropbox", "Notion", "Jira"])
    def test_sources_that_bring_in_shared_content_are_capped(self, connector_type: str) -> None:
        assert personal_cap_applies(connector_type)

    @pytest.mark.parametrize(
        "connector_type",
        ["S3", "MinIO", "GCS", "Azure Blob", "Azure Files", "SMB", "CIFS", "PostgreSQL",
         "MariaDB", "Snowflake", "Web", "RSS", "Local FS", "Local Repository"],
    )
    def test_storage_database_and_local_connectors_are_not(self, connector_type: str) -> None:
        assert not personal_cap_applies(connector_type)


class _SharedRedis:
    """What every connector process sees of one Redis: SET NX and release-if-owner."""

    def __init__(self, held: dict[str, str] | None = None) -> None:
        self.held = {} if held is None else held

    async def set(self, key: str, token: str, *, nx: bool, ex: int) -> bool:
        assert nx and ex > 0
        if key in self.held:
            return False
        self.held[key] = token
        return True

    async def eval(self, _script: str, _keys: int, key: str, token: str) -> int:
        if self.held.get(key) != token:
            return 0
        del self.held[key]
        return 1

    async def aclose(self) -> None:
        pass


def _registry(apps: list[dict]) -> tuple[ConnectorRegistry, AsyncMock]:
    container = MagicMock()
    container.logger.return_value = MagicMock()
    graph_provider = AsyncMock()
    graph_provider.get_org_apps.return_value = apps
    data_store = MagicMock()
    data_store.graph_provider = graph_provider
    container.data_store = AsyncMock(return_value=data_store)
    return ConnectorRegistry(container), graph_provider


@pytest.fixture
def redis():  # noqa: ANN201
    shared = _SharedRedis()
    with (
        patch(f"{_REGISTRY}.redis_from_config_service", AsyncMock(side_effect=lambda _c: _SharedRedis(shared.held))),
        patch(f"{_REGISTRY}._CAP_LOCK_WAIT_SECONDS", 0),
    ):
        yield shared


class TestTheCheckAtCreation:
    @pytest.mark.asyncio
    async def test_without_a_key_a_new_person_past_the_cap_is_refused(self, redis: _SharedRedis) -> None:
        registry, graph_provider = _registry(_others(PERSONAL_CONNECTOR_CAP))
        with patch(_GATE, AsyncMock(return_value=False)), pytest.raises(PersonalConnectorCapReachedError):
            await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        graph_provider.get_org_apps.assert_awaited_once_with(
            "org-1", active_only=False, app_type="Drive", raise_on_error=True
        )
        assert redis.held == {}

    @pytest.mark.asyncio
    async def test_below_the_cap_the_lock_is_held_until_the_caller_releases_it(self, redis: _SharedRedis) -> None:
        registry, _ = _registry(_others(PERSONAL_CONNECTOR_CAP - 1))
        with patch(_GATE, AsyncMock(return_value=False)):
            release = await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        assert list(redis.held) == ["personal_connector_cap:org-1:Drive"]
        await release()
        assert redis.held == {}

    @pytest.mark.asyncio
    async def test_with_a_key_there_is_no_limit_and_no_lock(self, redis: _SharedRedis) -> None:
        registry, graph_provider = _registry(_others(PERSONAL_CONNECTOR_CAP + 10))
        with patch(_GATE, AsyncMock(return_value=True)):
            assert await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1") is None
        graph_provider.get_org_apps.assert_not_awaited()
        assert redis.held == {}

    @pytest.mark.asyncio
    async def test_company_wide_and_uncapped_connectors_skip_the_check(self, redis: _SharedRedis) -> None:
        registry, graph_provider = _registry(_others(PERSONAL_CONNECTOR_CAP + 10))
        licensed = AsyncMock(return_value=False)
        with patch(_GATE, licensed):
            assert await registry._hold_personal_connector_cap("Drive", "team", "newcomer", "org-1") is None
            assert await registry._hold_personal_connector_cap("S3", "personal", "newcomer", "org-1") is None
        licensed.assert_not_awaited()
        graph_provider.get_org_apps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_listing_refuses_the_create_and_frees_the_lock(self, redis: _SharedRedis) -> None:
        registry, graph_provider = _registry([])
        graph_provider.get_org_apps.side_effect = RuntimeError("db down")
        with patch(_GATE, AsyncMock(return_value=False)), pytest.raises(PersonalConnectorCapUnavailableError):
            await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        assert redis.held == {}

    @pytest.mark.asyncio
    async def test_a_failed_licence_read_refuses_the_create(self, redis: _SharedRedis) -> None:
        registry, graph_provider = _registry([])
        failed = AsyncMock(side_effect=RuntimeError("config store down"))
        with patch(_GATE, failed), pytest.raises(PersonalConnectorCapUnavailableError):
            await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        graph_provider.get_org_apps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_lock_that_cannot_be_reached_refuses_the_create(self) -> None:
        registry, graph_provider = _registry([])
        unreachable = AsyncMock(side_effect=ConnectionError("redis down"))
        with (
            patch(_GATE, AsyncMock(return_value=False)),
            patch(f"{_REGISTRY}.redis_from_config_service", unreachable),
            pytest.raises(PersonalConnectorCapUnavailableError),
        ):
            await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        graph_provider.get_org_apps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_lock_someone_else_keeps_holding_refuses_the_create(self, redis: _SharedRedis) -> None:
        redis.held["personal_connector_cap:org-1:Drive"] = "another process"
        registry, graph_provider = _registry([])
        with patch(_GATE, AsyncMock(return_value=False)), pytest.raises(PersonalConnectorCapUnavailableError):
            await registry._hold_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        graph_provider.get_org_apps.assert_not_awaited()
        assert redis.held == {"personal_connector_cap:org-1:Drive": "another process"}

    def test_the_refusal_reaches_the_user_as_written(self) -> None:
        error = PersonalConnectorCapReachedError("Ask an admin to add a licence key to lift the limit.")
        assert _setup_failure_message(error) == str(error)


class _Org:
    """One organisation's connectors, as every connector process's store sees them.

    A connector is listed once its org edge is written, and writing the connector
    itself gives way to other requests first, which is when a second create would
    count the owners if nothing held it back.
    """

    def __init__(self, apps: list[dict]) -> None:
        self.apps = apps
        self._written: dict[str, dict] = {}

    def process(self) -> ConnectorRegistry:
        graph_provider = AsyncMock()
        graph_provider.get_document.return_value = {"_key": "org-1"}
        graph_provider.check_connector_name_exists.return_value = False
        graph_provider.get_org_apps.side_effect = lambda *_a, **_k: list(self.apps)
        graph_provider.batch_upsert_nodes.side_effect = self._write_app
        graph_provider.batch_create_edges.side_effect = self._write_edge
        registry, _ = _registry([])
        registry.container.data_store.return_value.graph_provider = graph_provider
        return registry

    async def _write_app(self, nodes: list[dict], _collection: str) -> bool:
        for _ in range(5):
            await asyncio.sleep(0)
        self._written.update({node["_key"]: node for node in nodes})
        return True

    async def _write_edge(self, edges: list[dict], collection: str) -> bool:
        assert collection == CollectionNames.ORG_APP_RELATION.value
        self.apps.extend(self._written[edge["to_id"]] for edge in edges)
        return True


async def _create(registry: ConnectorRegistry, created_by: str) -> dict | Exception | None:
    try:
        return await registry._create_connector_instance(
            "Drive", f"{created_by}'s Drive", {"appGroup": "Google", "supportedAuthTypes": ["OAUTH"]},
            "personal", created_by, "org-1", selected_auth_type="OAUTH",
        )
    except PersonalConnectorCapReachedError as refused:
        return refused


class TestTwoCreatesAtOnce:
    @pytest.mark.asyncio
    async def test_two_new_people_racing_for_the_last_place_get_one_connector(self, redis: _SharedRedis) -> None:
        org = _Org(_others(PERSONAL_CONNECTOR_CAP - 1))
        with (
            patch(_GATE, AsyncMock(return_value=False)),
            patch(f"{_REGISTRY}.assign_lane_to_new_connector", AsyncMock()),
        ):
            results = await asyncio.gather(_create(org.process(), "alice"), _create(org.process(), "bob"))

        refused = [r for r in results if isinstance(r, PersonalConnectorCapReachedError)]
        assert len(refused) == 1
        assert len({app["createdBy"] for app in org.apps}) == PERSONAL_CONNECTOR_CAP
        assert redis.held == {}

    @pytest.mark.asyncio
    async def test_a_create_that_fails_after_the_check_frees_the_lock(self, redis: _SharedRedis) -> None:
        org = _Org(_others(PERSONAL_CONNECTOR_CAP - 1))
        registry = org.process()
        with patch(_GATE, AsyncMock(return_value=False)), pytest.raises(ValueError, match="selected_auth_type"):
            await registry._create_connector_instance(
                "Drive", "My Drive", {"appGroup": "Google"}, "personal", "alice", "org-1"
            )
        assert redis.held == {}
