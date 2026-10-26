"""Knowledge bases one person creates at the same time are all created.

Each create writes edges from the same user node. On Neo4j, concurrent creates
deadlock on that node (Neo.TransientError.Transaction.DeadlockDetected), and the
nightly of 10/07 answered one of ten parallel creates with a 500. ArangoDB runs
the same case, where the collision is a write-write conflict.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kb_create_concurrency_it"
CREATES = 20

logger = logging.getLogger("kb-create-concurrency-it")


@pytest.fixture(params=["neo4j", "arango"])
async def owner(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[tuple[KnowledgeBaseService, str, str, list[str]]]:
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
        org_id = f"org-kbcreate-{run}"
        user_id, user_key = f"user-kbcreate-{run}", f"ukey-kbcreate-{run}"
        created: list[str] = []

        async def remove() -> None:
            for collection, keys in (
                (CollectionNames.APPS.value, created),
                (CollectionNames.USERS.value, [user_key]),
                (CollectionNames.ORGS.value, [org_id]),
            ):
                if keys:
                    with contextlib.suppress(Exception):
                        await graph.delete_nodes_and_edges(keys, collection)

        cleanup.push_async_callback(remove)
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "kb create"}],
            CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"_key": user_key, "userId": user_id, "orgId": org_id,
              "email": f"{user_id}@example.com", "isActive": True}],
            CollectionNames.USERS.value,
        )
        service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=AsyncMock())
        yield service, user_id, org_id, created


async def test_concurrent_creates_by_one_user_all_succeed(owner) -> None:
    service, user_id, org_id, created = owner
    names = [f"kbcreate {i:02d} {uuid.uuid4().hex[:6]}" for i in range(CREATES)]

    results = await asyncio.gather(*(
        service.create_knowledge_base(user_id=user_id, org_id=org_id, name=name) for name in names
    ))
    created.extend(result["id"] for result in results if result.get("success"))

    failed = [(name, result) for name, result in zip(names, results) if not result.get("success")]
    assert not failed, f"{len(failed)} of {CREATES} concurrent creates failed: {failed[:3]}"

    listed = await service.list_user_knowledge_bases(
        user_id=user_id, org_id=org_id, page=1, limit=100,
    )
    assert isinstance(listed, dict) and "knowledgeBases" in listed, listed
    assert sorted(kb["id"] for kb in listed["knowledgeBases"]) == sorted(created), (
        "every created knowledge base is in its owner's list exactly once"
    )
