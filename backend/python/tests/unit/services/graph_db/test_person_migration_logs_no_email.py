"""`migrate_person_to_user` runs on every userAdded and for each new synced app user; its log lines, and its callers' failure
lines, name the user key, never the email."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.models.entities import AppUser, Person
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

LOGGER_NAME = "tests.graph_db.person_migration_logs"
EMAIL = "alice@example.com"


@pytest.fixture(autouse=True)
def _capture(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)


def _messages(caplog: pytest.LogCaptureFixture, level: int = logging.DEBUG) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME and r.levelno >= level]


def _arango() -> ArangoHTTPProvider:
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logging.getLogger(LOGGER_NAME)
    provider.http_client = AsyncMock()
    return provider


def _neo4j(execute_query: AsyncMock) -> Neo4jProvider:
    provider = Neo4jProvider.__new__(Neo4jProvider)
    provider.logger = logging.getLogger(LOGGER_NAME)
    provider.client = MagicMock()
    provider.client.execute_query = execute_query
    return provider


async def test_arango_migration_logs_person_and_user_keys(caplog: pytest.LogCaptureFixture) -> None:
    provider = _arango()
    provider.get_person_by_email = AsyncMock(return_value=Person(id="p1", email=EMAIL))
    # CRM probe, then the permission and userAppRelation transfers, each with nothing to move.
    provider.http_client.execute_aql = AsyncMock(
        side_effect=[[False], [{"mine": [], "theirs": []}], [{"mine": [], "theirs": []}], []]
    )

    assert await provider.migrate_person_to_user(EMAIL, "u1", "org-1", transaction="txn-1") is not None

    messages = _messages(caplog)
    assert not any(EMAIL in m for m in messages)
    assert "Person p1 -> user u1: migrated (0 edge(s) moved)" in messages


async def test_arango_migration_failure_logs_user_key(caplog: pytest.LogCaptureFixture) -> None:
    provider = _arango()
    provider.get_person_by_email = AsyncMock(side_effect=RuntimeError("graph down"))

    with pytest.raises(RuntimeError):
        await provider.migrate_person_to_user(EMAIL, "u1", "org-1", transaction="txn-1")

    assert not any(EMAIL in m for m in _messages(caplog))
    assert _messages(caplog, logging.ERROR) == ["Migrate person to user failed for user u1 in org org-1: graph down"]


async def test_neo4j_migration_logs_user_key(caplog: pytest.LogCaptureFixture) -> None:
    row = {"mode": "migrated", "moved_permissions": 2, "moved_app_relations": 1}
    provider = _neo4j(AsyncMock(return_value=[row]))

    assert await provider.migrate_person_to_user(EMAIL, "u1", "org-1") == "migrated"

    messages = _messages(caplog)
    assert not any(EMAIL in m for m in messages)
    assert "Person -> user u1: migrated (2 permission, 1 app-relation edges moved)" in messages


async def test_neo4j_migration_failure_logs_user_key(caplog: pytest.LogCaptureFixture) -> None:
    provider = _neo4j(AsyncMock(side_effect=RuntimeError("graph down")))

    with pytest.raises(RuntimeError):
        await provider.migrate_person_to_user(EMAIL, "u1", "org-1")

    assert not any(EMAIL in m for m in _messages(caplog))
    assert _messages(caplog, logging.ERROR) == ["Migrate person to user failed for user u1 in org org-1: graph down"]


# ---- callers: batch_upsert_app_users adopts a Person for each new app user, and a failed adoption is logged, not raised ----

def _app_user(*, is_guest: bool = False) -> AppUser:
    return AppUser(
        app_name=Connectors.SLACK, connector_id="c1", id="u1", source_user_id="src-1",
        email=EMAIL, full_name="Alice Liddell", is_guest=is_guest,
    )


def _assert_adoption_failure_logged_by_id(caplog: pytest.LogCaptureFixture) -> None:
    messages = _messages(caplog)
    assert not any(EMAIL in m or "Alice Liddell" in m for m in messages)
    assert _messages(caplog, logging.ERROR) == ["Person adoption failed for user u1 in org org-1: graph down"]


@pytest.mark.parametrize("provider_cls", [ArangoHTTPProvider, Neo4jProvider])
async def test_batch_upsert_app_users_logs_a_failed_adoption_by_user_id(provider_cls, caplog: pytest.LogCaptureFixture) -> None:
    provider = provider_cls.__new__(provider_cls)
    provider.logger = logging.getLogger(LOGGER_NAME)
    provider.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1", "id": "org-1"}])
    provider.get_document = AsyncMock(return_value={"_id": "apps/c1", "id": "c1"})
    provider.get_user_by_email = AsyncMock(side_effect=[None, MagicMock(id="u1", is_active=False)])
    provider.batch_upsert_nodes = AsyncMock()
    provider.batch_create_edges = AsyncMock()
    provider.migrate_person_to_user = AsyncMock(side_effect=RuntimeError("graph down"))

    await provider.batch_upsert_app_users([_app_user()], transaction="txn-1")

    _assert_adoption_failure_logged_by_id(caplog)


@pytest.mark.parametrize("provider_cls", [ArangoHTTPProvider, Neo4jProvider])
async def test_batch_upsert_app_users_logs_an_active_guest_by_graph_key(provider_cls, caplog: pytest.LogCaptureFixture) -> None:
    """A guest who already exists and is active keeps the org edge; the line names the stored user, not the address."""
    provider = provider_cls.__new__(provider_cls)
    provider.logger = logging.getLogger(LOGGER_NAME)
    provider.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1", "id": "org-1"}])
    provider.get_document = AsyncMock(return_value={"_id": "apps/c1", "id": "c1"})
    provider.get_user_by_email = AsyncMock(return_value=MagicMock(id="graph-u1", is_active=True))
    provider.batch_create_edges = AsyncMock()
    provider.delete_edge = AsyncMock()

    await provider.batch_upsert_app_users([_app_user(is_guest=True)], transaction="txn-1")

    messages = _messages(caplog)
    assert not any(EMAIL in m or "Alice Liddell" in m for m in messages)
    assert "Guest user graph-u1 is active in PipesHub; keeping their organization membership" in messages
    provider.delete_edge.assert_not_awaited()


async def test_postgres_batch_upsert_app_users_logs_a_failed_adoption_by_user_id(caplog: pytest.LogCaptureFixture) -> None:
    pytest.importorskip("app.services.graph_db.postgres.provider")
    from app.core.org_context import OrgContext, bind
    from app.services.graph_db.postgres.provider import PostgresGraphProvider

    provider = PostgresGraphProvider(logging.getLogger(LOGGER_NAME), MagicMock())
    provider.get_all_orgs = AsyncMock(return_value=[{"id": "org-1"}])
    provider._q = AsyncMock()
    # lower(email) for the batch, then no existing user with that address.
    provider._id_rows = AsyncMock(side_effect=[[{"raw": EMAIL, "low": EMAIL}], []])
    provider.batch_upsert_nodes = AsyncMock()
    provider.batch_create_edges = AsyncMock()
    provider.migrate_person_to_user = AsyncMock(side_effect=RuntimeError("graph down"))

    with bind(OrgContext("org-1", "user-1", "job", "job:test", "t")):
        await provider.batch_upsert_app_users([_app_user()], transaction="txn-1")

    _assert_adoption_failure_logged_by_id(caplog)
