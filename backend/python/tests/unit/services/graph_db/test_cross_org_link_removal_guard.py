"""The method that removes links in every org refuses to run for a request: only an explicit system context may call it."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, SystemContextNotAllowed, bind, system
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango() -> ArangoHTTPProvider:
    provider = object.__new__(ArangoHTTPProvider)
    provider.execute_query = AsyncMock(return_value=[])
    return provider


def _neo4j() -> Neo4jProvider:
    provider = object.__new__(Neo4jProvider)
    provider.client = MagicMock(execute_query=AsyncMock(return_value=[]))
    return provider


@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
class TestOnlyASystemContextMayRemoveLinksInEveryOrg:
    async def test_no_context_is_refused(self, make) -> None:
        provider = make()
        with pytest.raises(SystemContextNotAllowed):
            await provider.remove_listed_authenticator_links(["Dropbox"])

    async def test_a_request_bound_to_one_org_is_refused(self, make) -> None:
        provider = make()
        with bind(OrgContext("org-1", "user-1", "user", "test", "req-1")), pytest.raises(SystemContextNotAllowed):
            await provider.remove_listed_authenticator_links(["Dropbox"])

    async def test_a_system_context_is_allowed(self, make) -> None:
        provider = make()
        with system("test"):
            assert await provider.remove_listed_authenticator_links(["Dropbox"]) == ([], [])
