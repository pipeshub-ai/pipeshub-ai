"""`ensure_all_team_with_users` runs per org in the All-team migration; it may log member counts, not member emails."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

LOGGER_NAME = "tests.graph_db.all_team_logs"
EMAILS = ("alice@example.com", "bob@example.com")


def _with_members(provider: ArangoHTTPProvider | Neo4jProvider) -> ArangoHTTPProvider | Neo4jProvider:
    provider.logger = logging.getLogger(LOGGER_NAME)
    provider.get_document = AsyncMock(return_value={"id": "all_org-1"})
    provider.get_users = AsyncMock(return_value=[{"_key": "u1"}, {"_key": "u2"}])
    provider.get_team_with_users = AsyncMock(return_value={
        "members": [{"userEmail": EMAILS[0], "role": "OWNER"}, {"userEmail": EMAILS[1], "role": "READER"}],
    })
    # Every user already has its edge, so the run only reads.
    provider.get_edge = AsyncMock(return_value={"_key": "edge"})
    provider.client = MagicMock()
    provider.client.execute_query = AsyncMock(return_value=[{"r": {}}])
    return provider


@pytest.mark.parametrize("provider_cls", [ArangoHTTPProvider, Neo4jProvider])
async def test_member_count_is_logged_not_member_emails(provider_cls, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER_NAME)
    provider = _with_members(provider_cls.__new__(provider_cls))

    await provider.ensure_all_team_with_users("org-1")

    messages = [r.getMessage() for r in caplog.records if r.name == LOGGER_NAME]
    assert any("existing_member_count=2" in m for m in messages)
    for email in EMAILS:
        assert not any(email in m for m in messages), f"{email} was logged"
    assert not [r for r in caplog.records if r.name == LOGGER_NAME and r.levelno >= logging.ERROR]
