"""get_record_by_issue_key finds exactly the asked-for issue on real Neo4j and ArangoDB.

The lookup used to match "/browse/{key}" anywhere in the webUrl with LIMIT 1, so
ENG-1 could come back as ENG-12, and the Jira deletion paths then deleted ENG-12.
ENG-12 and ENG-123 are written before ENG-1 so a loose match meets them first.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import RecordType, TicketRecord
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "jira_issue_key_lookup_it"
SITE = "https://acme.atlassian.net"

logger = logging.getLogger("jira-issue-key-lookup-it")


@dataclass
class _Tickets:
    graph: IGraphDBProvider
    connector_id: str
    ids: dict[str, str]


def _ticket(org_id: str, connector_id: str, key: str, weburl: str) -> TicketRecord:
    return TicketRecord(
        id=str(uuid.uuid4()), org_id=org_id, record_name=f"{key} summary",
        record_type=RecordType.TICKET, external_record_id=f"ext-{key}", version=1,
        origin=OriginTypes.CONNECTOR, connector_name=Connectors.JIRA, connector_id=connector_id,
        weburl=weburl,
    )


@pytest.fixture(params=["neo4j", "arango"])
async def tickets(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Tickets]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id, connector_id = f"org-jik-{run}", f"conn-jik-{run}"
        rows = [
            ("ENG-12", f"{SITE}/browse/ENG-12"),
            ("ENG-123", f"{SITE}/browse/ENG-123"),
            ("ENG-10-comment", f"{SITE}/browse/ENG-10?focusedCommentId=1"),
            ("ENG-1", f"{SITE}/browse/ENG-1"),
        ]
        records = [_ticket(org_id, connector_id, key, url) for key, url in rows]
        ids = {key: record.id for (key, _url), record in zip(rows, records, strict=True)}

        async def remove() -> None:
            with contextlib.suppress(Exception):
                for collection in (CollectionNames.TICKETS.value, CollectionNames.RECORDS.value):
                    await graph.delete_nodes_and_edges(list(ids.values()), collection)

        cleanup.push_async_callback(remove)
        for record in records:
            assert await graph.batch_upsert_nodes([record.to_arango_base_record()], CollectionNames.RECORDS.value)
            assert await graph.batch_upsert_nodes([record.to_arango_record()], CollectionNames.TICKETS.value)
        yield _Tickets(graph, connector_id, ids)


async def test_a_key_finds_its_own_issue_not_a_longer_one(tickets: _Tickets) -> None:
    found = await tickets.graph.get_record_by_issue_key(tickets.connector_id, "ENG-1")

    assert found is not None
    assert found.id == tickets.ids["ENG-1"], f"ENG-1 resolved to {found.weburl}"


async def test_a_key_with_no_issue_of_its_own_finds_nothing(tickets: _Tickets) -> None:
    await tickets.graph.delete_nodes_and_edges([tickets.ids["ENG-1"]], CollectionNames.RECORDS.value)

    found = await tickets.graph.get_record_by_issue_key(tickets.connector_id, "ENG-1")

    assert found is None, f"ENG-1 resolved to {found.weburl}"


async def test_longer_keys_still_find_themselves(tickets: _Tickets) -> None:
    for key in ("ENG-12", "ENG-123"):
        found = await tickets.graph.get_record_by_issue_key(tickets.connector_id, key)
        assert found is not None and found.id == tickets.ids[key], key
