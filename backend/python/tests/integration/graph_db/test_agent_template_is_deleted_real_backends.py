"""An agent or agent template with no ``isDeleted`` field is live, on a real Neo4j and ArangoDB.

The template owner check of ``update_agent_template`` / ``delete_agent_template`` on ArangoDB kept only templates whose
``isDeleted`` equals false, so its owner could not edit or delete a template stored without the field, though the list and
``get_template`` showed it. Every read and write here treats a missing field as not deleted and ``isDeleted: true`` as deleted.
The PostgreSQL provider runs the reads it has in ``postgres/test_agent_is_deleted.py``.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from typing import TYPE_CHECKING

import pytest

from app.config.constants.arangodb import CollectionNames
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "agent_template_is_deleted_it"
ORGS, USERS = CollectionNames.ORGS.value, CollectionNames.USERS.value
AGENTS, TEMPLATES, PERMISSION = CollectionNames.AGENT_INSTANCES.value, CollectionNames.AGENT_TEMPLATES.value, CollectionNames.PERMISSION.value

logger = logging.getLogger("agent-template-is-deleted-it")


def _owner(user: str, coll: str, key: str) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {"from_id": user, "from_collection": USERS, "to_id": key, "to_collection": coll, "type": "USER", "role": "OWNER",
            "createdAtTimestamp": now, "updatedAtTimestamp": now}


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[IGraphDBProvider, dict[str, str]]]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        run = uuid.uuid4().hex[:10]
        ids = {k: f"{k}-isdel-{run}" for k in ("org", "owner", "tpl_bare", "tpl_dead", "agent_bare", "agent_dead")}
        seeded = {ORGS: [ids["org"]], USERS: [ids["owner"]], TEMPLATES: [ids["tpl_bare"], ids["tpl_dead"]], AGENTS: [ids["agent_bare"], ids["agent_dead"]]}

        async def remove() -> None:
            for coll, keys in seeded.items():
                with contextlib.suppress(Exception):
                    await graph.delete_nodes_and_edges(keys, coll)
        cleanup.push_async_callback(remove)
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes([{"_key": ids["org"], "accountType": "enterprise", "isActive": True, "name": "is deleted"}], ORGS)
        assert await graph.batch_upsert_nodes([{"_key": ids["owner"], "userId": f"uid-{ids['owner']}", "orgId": ids["org"], "email": f"owner-{run}@example.com",
                                                "isActive": True}], USERS)
        template = {"description": "d", "startMessage": "s", "systemPrompt": "p", "orgId": ids["org"], "isActive": True, "createdBy": ids["owner"], "createdAtTimestamp": now}
        assert await graph.batch_upsert_nodes([{"_key": ids["tpl_bare"], "name": "bare", **template},                       # no isDeleted field at all
                                               {"_key": ids["tpl_dead"], "name": "dead", "isDeleted": True, **template}], TEMPLATES)
        agent = {"description": "d", "startMessage": "s", "systemPrompt": "p", "models": [], "createdBy": ids["owner"], "createdAtTimestamp": now, "updatedAtTimestamp": now}
        assert await graph.batch_upsert_nodes([{"_key": ids["agent_bare"], "name": "bare", **agent},
                                               {"_key": ids["agent_dead"], "name": "dead", "isDeleted": True, **agent}], AGENTS)
        assert await graph.batch_create_edges([_owner(ids["owner"], TEMPLATES, ids["tpl_bare"]), _owner(ids["owner"], TEMPLATES, ids["tpl_dead"]),
                                               _owner(ids["owner"], AGENTS, ids["agent_bare"]), _owner(ids["owner"], AGENTS, ids["agent_dead"])], PERMISSION)
        assert "isDeleted" not in await graph.get_document(ids["tpl_bare"], TEMPLATES)
        assert "isDeleted" not in await graph.get_document(ids["agent_bare"], AGENTS)
        yield graph, ids


async def test_a_template_without_the_field_is_live_for_its_owner_and_a_deleted_one_is_not(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    owner = ids["owner"]
    assert [t["_key"] for t in await graph.get_all_agent_templates(owner) if t["_key"] in (ids["tpl_bare"], ids["tpl_dead"])] == [ids["tpl_bare"]]
    assert (await graph.get_template(ids["tpl_bare"], owner))["can_delete"] is True and await graph.get_template(ids["tpl_dead"], owner) is None
    assert await graph.update_agent_template(ids["tpl_bare"], {"name": "renamed"}, owner)
    assert (await graph.get_document(ids["tpl_bare"], TEMPLATES))["name"] == "renamed"
    assert not await graph.update_agent_template(ids["tpl_dead"], {"name": "x"}, owner)
    assert not await graph.delete_agent_template(ids["tpl_dead"], owner)
    assert (await graph.get_document(ids["tpl_dead"], TEMPLATES))["name"] == "dead"
    assert await graph.delete_agent_template(ids["tpl_bare"], owner)
    assert (await graph.get_document(ids["tpl_bare"], TEMPLATES))["isDeleted"] is True
    assert not await graph.update_agent_template(ids["tpl_bare"], {"name": "again"}, owner)                    # deleted now: no longer editable


async def test_an_agent_without_the_field_is_live_for_its_owner_and_a_deleted_one_is_not(world: tuple[IGraphDBProvider, dict[str, str]]) -> None:
    graph, ids = world
    owner, org = ids["owner"], ids["org"]
    assert (await graph.check_agent_permission(ids["agent_bare"], owner, org))["user_role"] == "OWNER"
    assert await graph.check_agent_permission(ids["agent_dead"], owner, org) is None
    assert (await graph.get_agent(ids["agent_bare"], org))["name"] == "bare" and await graph.get_agent(ids["agent_dead"], org) is None
    listed = await graph.get_all_agents(owner, org)
    assert [a["_key"] for a in listed if a["_key"] in (ids["agent_bare"], ids["agent_dead"])] == [ids["agent_bare"]]
    deleted = await graph.get_all_agents(owner, org, is_deleted=True)
    assert [a["_key"] for a in deleted if a["_key"] in (ids["agent_bare"], ids["agent_dead"])] == [ids["agent_dead"]]
    assert await graph.update_agent(ids["agent_bare"], {"name": "renamed"}, owner, org)
    assert not await graph.update_agent(ids["agent_dead"], {"name": "x"}, owner, org)
    assert not await graph.delete_agent(ids["agent_dead"], owner, org)
    assert await graph.delete_agent(ids["agent_bare"], owner, org)
    assert (await graph.get_document(ids["agent_bare"], AGENTS))["isDeleted"] is True
