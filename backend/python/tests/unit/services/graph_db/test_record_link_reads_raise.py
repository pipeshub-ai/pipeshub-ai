"""A failed read behind a cross-tool join raises; it never looks like "nothing there".

The processor deletes a record's link edges and rewrites them from these reads,
so an empty answer from a failed query would drop edges that are still true.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(MagicMock(), MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(side_effect=RuntimeError("graph down"))
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=RuntimeError("graph down"))
    return p


READS = [
    ("get_records_by_weburls", (["https://github.com/a/b/pull/1"], "org-1")),
    ("get_records_linking_to_weburls", (["https://github.com/a/b/pull/1"], "org-1", ["SLACK WORKSPACE"])),
    ("get_issue_sites", ("org-1", ["JIRA"])),
    ("get_address_links_batch", (["pr-1"], "org-1")),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("make", [_arango, _neo4j], ids=["arango", "neo4j"])
@pytest.mark.parametrize(("method", "args"), READS, ids=[name for name, _ in READS])
async def test_a_failed_read_raises(make, method: str, args: tuple) -> None:
    with pytest.raises(RuntimeError, match="graph down"):
        await getattr(make(), method)(*args)
