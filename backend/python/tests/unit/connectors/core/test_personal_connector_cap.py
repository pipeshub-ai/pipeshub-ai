"""Without a licence key, a personal connector of each source is limited to a few people."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.api.router import _setup_failure_message
from app.connectors.core.base.data_processor.permission_sync_gate import (
    PERSONAL_CONNECTOR_CAP,
    PersonalConnectorCapReachedError,
    personal_cap_applies,
    personal_cap_reached,
)
from app.connectors.core.registry.connector_registry import ConnectorRegistry

_GATE = "app.connectors.core.registry.connector_registry.permission_sync_licensed"


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


def _registry(apps: list[dict]) -> tuple[ConnectorRegistry, AsyncMock]:
    container = MagicMock()
    container.logger.return_value = MagicMock()
    graph_provider = AsyncMock()
    graph_provider.get_org_apps.return_value = apps
    data_store = MagicMock()
    data_store.graph_provider = graph_provider
    container.data_store = AsyncMock(return_value=data_store)
    return ConnectorRegistry(container), graph_provider


class TestTheCheckAtCreation:
    @pytest.mark.asyncio
    async def test_without_a_key_a_new_person_past_the_cap_is_refused(self) -> None:
        registry, graph_provider = _registry(_others(PERSONAL_CONNECTOR_CAP))
        with patch(_GATE, AsyncMock(return_value=False)), pytest.raises(PersonalConnectorCapReachedError):
            await registry._check_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        graph_provider.get_org_apps.assert_awaited_once_with(
            "org-1", active_only=False, app_type="Drive", raise_on_error=True
        )

    @pytest.mark.asyncio
    async def test_with_a_key_there_is_no_limit(self) -> None:
        registry, graph_provider = _registry(_others(PERSONAL_CONNECTOR_CAP + 10))
        with patch(_GATE, AsyncMock(return_value=True)):
            await registry._check_personal_connector_cap("Drive", "personal", "newcomer", "org-1")
        graph_provider.get_org_apps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_company_wide_and_uncapped_connectors_skip_the_check(self) -> None:
        registry, graph_provider = _registry(_others(PERSONAL_CONNECTOR_CAP + 10))
        licensed = AsyncMock(return_value=False)
        with patch(_GATE, licensed):
            await registry._check_personal_connector_cap("Drive", "team", "newcomer", "org-1")
            await registry._check_personal_connector_cap("S3", "personal", "newcomer", "org-1")
        licensed.assert_not_awaited()
        graph_provider.get_org_apps.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failed_lookup_does_not_block_setup(self) -> None:
        registry, graph_provider = _registry([])
        graph_provider.get_org_apps.side_effect = RuntimeError("db down")
        with patch(_GATE, AsyncMock(return_value=False)):
            await registry._check_personal_connector_cap("Drive", "personal", "newcomer", "org-1")

    def test_the_refusal_reaches_the_user_as_written(self) -> None:
        error = PersonalConnectorCapReachedError("Ask an admin to add a licence key to lift the limit.")
        assert _setup_failure_message(error) == str(error)
