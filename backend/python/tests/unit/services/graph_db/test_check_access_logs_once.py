"""``check_access`` adds no line for a failure its graph client already recorded, on each store's real client path.

Each store's provider runs ``_kh_v3_accessible_rows`` for real over its real client wrapper; only the transport below the
client fails (an aiohttp session, the Neo4j driver call, an asyncpg connection). So what reaches ``check_access`` is what
the store really raises: Arango's recorded error itself, Neo4j's ``KnowledgeHubListingBusy`` raised ``from`` the recorded
error, PostgreSQL's mapped error stamped by ``Tx._run``. Exactly one ``db.query.failed`` is written, and no
``check_access failed`` line.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from neo4j.exceptions import Neo4jError

from app.core.db import uow
from app.core.org_context import OrgContext, bind
from app.exceptions.graph_db_exceptions import (
    KnowledgeHubListingBusy,
    PermissionVerificationUnavailableError,
)
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from tests.unit.core.db import _fake_kernel

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "65f0aaaaaaaaaaaaaaaaaaaa"
ACCESS = {"grantee_ids": ["u"], "gated_app_ids": ["app"], "by_connector": {"app": ["g1"]}}
DRIVER_TEXT = "graph-db.internal.example:7687 MATCH (u:User {id: 'gk-7f3a9c'})"


def _arango() -> ArangoHTTPProvider:
    client = ArangoHTTPClient("http://localhost:8529", "root", "pw", "db", logging.getLogger("test.check_once.arango"))
    resp = MagicMock(status=503)
    resp.text = AsyncMock(return_value=DRIVER_TEXT)
    resp.json = AsyncMock(return_value={"error": True, "errorNum": 503, "errorMessage": DRIVER_TEXT})
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=resp)
    ctx.__aexit__ = AsyncMock(return_value=False)
    session = MagicMock()
    session.post.return_value = ctx
    client._get_session = AsyncMock(return_value=session)
    provider = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
    provider.logger = logging.getLogger("test.check_once.provider")
    provider.http_client = client
    return provider


def _neo4j() -> Neo4jProvider:
    client = Neo4jClient(uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j",
                         logger=logging.getLogger("test.check_once.neo4j"))
    client.driver = MagicMock()
    out_of_memory = Neo4jError._hydrate_neo4j(code="Neo.TransientError.General.MemoryPoolOutOfMemoryError", message=DRIVER_TEXT)
    client._run_autocommit = AsyncMock(side_effect=out_of_memory)  # type: ignore[method-assign]
    provider = Neo4jProvider(logger=logging.getLogger("test.check_once.provider"), config_service=MagicMock())
    provider.client = client
    return provider


def _postgres() -> PostgresGraphProvider:
    class _Con:
        async def fetch(self, sql: str, *args: object) -> list:
            e = asyncpg.exceptions.InternalServerError(DRIVER_TEXT)
            e.sqlstate = "XX000"
            raise e

        def is_in_transaction(self) -> bool:
            return True

    return PostgresGraphProvider(logging.getLogger("test.check_once.provider"), _fake_kernel.TxnDb(_Con()))


@pytest.fixture
def postgres_unit(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """The provider reads only in its org, through the walk alone (kg off), with the fake kernel playing app.core.db."""
    monkeypatch.setenv("PG_KG_READ", "0")
    monkeypatch.setattr(uow, "QUERY_LOG_SKIP", (*uow.QUERY_LOG_SKIP, _fake_kernel.__name__))
    with bind(OrgContext(ORG, None, "job", "job:test", "t")):
        yield


STORES = {"arango": _arango, "neo4j": _neo4j, "postgres": _postgres}


@pytest.mark.asyncio
@pytest.mark.parametrize("store", STORES)
async def test_a_failure_the_client_recorded_gets_no_check_access_line(
    store: str, caplog: pytest.LogCaptureFixture, postgres_unit: None,
) -> None:
    provider = STORES[store]()
    with caplog.at_level(logging.DEBUG), pytest.raises(PermissionVerificationUnavailableError) as raised:
        await provider.check_access("u", ORG, node_ids=["n1"], access=ACCESS)
    if store == "neo4j":  # the wrapper the outermost-only check missed
        assert isinstance(raised.value.__cause__, KnowledgeHubListingBusy)
    messages = [r.getMessage() for r in caplog.records]
    assert messages.count("db.query.failed") == 1, messages
    assert not [m for m in messages if m.startswith("check_access failed")], messages
    assert not [r for r in caplog.records if DRIVER_TEXT in r.getMessage()]
