"""`migrate_person_to_user` runs on every userAdded; its log lines name the user key, never the email."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.entities import Person
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
