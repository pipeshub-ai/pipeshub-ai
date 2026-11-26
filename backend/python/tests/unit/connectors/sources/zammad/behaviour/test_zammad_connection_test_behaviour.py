"""The connection test says what is wrong with a Zammad token.

A token Zammad rejects, or one that may not list users, groups or roles (before
Zammad 6.5 groups and roles are listed only to admin.group and admin.role), raises
``ConnectorInitError`` with a message the setup screen shows. Any other failure
returns False.

The connector and the real ``ZammadDataSource`` run as they do in production,
over an in-memory Zammad answering HTTP requests.
"""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from zammad_behaviour_fakes import (
    FakeConfigService,
    FakeDirectory,
    FakeStore,
    FakeZammadPeople,
)

from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.sources.zammad.connector import ZammadConnector
from app.sources.external.zammad.zammad import ZammadDataSource

ME, USERS, GROUPS, ROLES = "/api/v1/users/me", "/api/v1/users", "/api/v1/groups", "/api/v1/roles"


def _connector(zammad: FakeZammadPeople) -> ZammadConnector:
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=logging.getLogger("test.zammad.connection"),
            data_entities_processor=FakeDirectory(),
            data_store_provider=FakeStore(),
            config_service=FakeConfigService(),
            connector_id="zm-connection",
            scope="team",
            created_by="admin",
        )
    client = MagicMock()
    client.get_client.return_value = zammad
    client.get_base_url.return_value = "https://zammad.test"
    connector._get_fresh_datasource = AsyncMock(return_value=ZammadDataSource(client))
    return connector


@pytest.fixture
def zammad() -> FakeZammadPeople:
    people = FakeZammadPeople({1: "Support"})
    people.add_user(1)
    return people


async def test_a_token_that_may_read_users_groups_and_roles_passes(zammad: FakeZammadPeople) -> None:
    assert await _connector(zammad).test_connection_and_access() is True

    assert [path for path, _ in zammad.calls] == [ME, USERS, GROUPS, ROLES]


@pytest.mark.parametrize("status", [401, 403])
async def test_a_token_zammad_does_not_accept_names_the_token(zammad: FakeZammadPeople, status: int) -> None:
    zammad.path_status[ME] = status

    with pytest.raises(ConnectorInitError, match="did not accept the access token") as raised:
        await _connector(zammad).test_connection_and_access()

    assert "API token access" in str(raised.value)


@pytest.mark.parametrize(("path", "what", "permission"), [
    (USERS, "users", "ticket.agent"),
    (GROUPS, "groups", "admin.group"),
    (ROLES, "roles", "admin.role"),
])
async def test_a_list_zammad_refuses_names_the_permission(
    zammad: FakeZammadPeople, path: str, what: str, permission: str,
) -> None:
    zammad.path_status[path] = 403

    with pytest.raises(ConnectorInitError) as raised:
        await _connector(zammad).test_connection_and_access()

    message = str(raised.value)
    assert f"the list of {what}" in message
    assert f"the {permission} permission" in message
    assert ("before 6.5" in message) is (what != "users")


@pytest.mark.parametrize("path", [ME, USERS, GROUPS, ROLES])
async def test_any_other_failure_returns_false(
    zammad: FakeZammadPeople, path: str, caplog: pytest.LogCaptureFixture,
) -> None:
    zammad.path_status[path] = 502

    with caplog.at_level(logging.ERROR):
        assert await _connector(zammad).test_connection_and_access() is False

    assert any("HTTP 502" in r.getMessage() for r in caplog.records)


async def test_a_connection_that_cannot_be_made_returns_false(zammad: FakeZammadPeople) -> None:
    connector = _connector(zammad)
    connector._get_fresh_datasource = AsyncMock(side_effect=ConnectionError("no route to host"))

    assert await connector.test_connection_and_access() is False



async def test_the_connector_builds_its_client_with_its_rate_limit_and_retries() -> None:
    """Zammad has no rate limit of its own; the declared one keeps a sync from crowding out the agents."""
    with patch("app.connectors.sources.zammad.connector.ZammadApp"):
        connector = ZammadConnector(
            logger=logging.getLogger("test.zammad.connection"),
            data_entities_processor=FakeDirectory(),
            data_store_provider=FakeStore(),
            config_service=FakeConfigService(),
            connector_id="zm-connection",
            scope="team",
            created_by="admin",
        )
    connector._load_lookup_tables = AsyncMock()

    assert await connector.init() is True

    policy = connector.resilience
    assert (policy.rate_limit, policy.max_retries, policy.max_delay) == (20, 3, 30.0)
    assert connector.external_client.get_client().resilience is policy

    # A token changed in the config rebuilds the client, under the same policy.
    connector.external_client.get_client().token = "an older token"
    await connector._get_fresh_datasource()
    assert connector.external_client.get_client().token == "t"
    assert connector.external_client.get_client().resilience is policy
