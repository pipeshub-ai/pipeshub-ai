"""``get_user_by_user_id`` bound to the asking organisation, against a real Neo4j and a real ArangoDB.

With the org the lookup is bound to it, a request bound to another org finds none of this org's users, an empty org
matches no user, and without an org the lookup answers as it always did. Enforce mode (the default), so the ambient
org check runs as in a service. PostgreSQL: ``postgres/test_user_lookup_asking_org.py``.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgMismatch, for_org
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

USERS = CollectionNames.USERS.value
logger = logging.getLogger(__name__)


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[dict]:
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                           else connect_arango(logger, "user_lookup_asking_org_it"))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        if (disconnect := getattr(graph, "disconnect", None)) is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        w = {"graph": graph, "org": f"org-ul-{run}", "user_id": f"uid-{run}", "key": f"uk-{run}"}
        assert await graph.batch_upsert_nodes(
            [{"_key": w["key"], "userId": w["user_id"], "orgId": w["org"], "email": f"{run}@example.com",
              "isActive": True}],
            USERS,
        )

        async def _remove() -> None:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges([w["key"]], USERS)

        cleanup.push_async_callback(_remove)
        yield w


def _key(user: dict | None) -> str | None:
    return None if user is None else user.get("_key") or user.get("id")


async def test_the_asking_org_finds_its_own_user(world: dict) -> None:
    graph = world["graph"]
    with for_org(world["org"], job="it"):
        assert _key(await graph.get_user_by_user_id(world["user_id"], org_id=world["org"])) == world["key"]


async def test_without_an_org_the_lookup_is_unchanged(world: dict) -> None:
    graph = world["graph"]
    assert _key(await graph.get_user_by_user_id(world["user_id"])) == world["key"]
    with for_org(world["org"], job="it"):
        assert _key(await graph.get_user_by_user_id(world["user_id"])) == world["key"]


async def test_an_empty_org_matches_no_user(world: dict) -> None:
    graph = world["graph"]
    with for_org(world["org"], job="it"):
        assert await graph.get_user_by_user_id(world["user_id"], org_id="") is None


async def test_another_org_is_refused(world: dict) -> None:
    graph, other = world["graph"], f"{world['org']}-other"
    with for_org(other, job="it"):
        assert await graph.get_user_by_user_id(world["user_id"], org_id=other) is None
    with for_org(world["org"], job="it"), pytest.raises(OrgMismatch):
        await graph.get_user_by_user_id(world["user_id"], org_id=other)
