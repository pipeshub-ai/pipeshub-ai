"""The agent's full-record tool lists only live foreign-key neighbours: real Neo4j and ArangoDB.

Opening a database table with ``fetch_full_record`` also lists the tables it
is linked to by a foreign key, with their record ids, table names and columns,
so the agent can open them next. The trash keeps a dropped table's node and
its foreign-key edges until the purge, so the tool has to check that each
neighbour is live before naming it.

The four tables of tests/integration/test_soft_delete_chat_fk_e2e.py are
written by the production sync path (``on_new_records``) and a dropped one is
trashed by the connector's own delete (``on_record_deleted``) with
``ENABLE_SOFT_DELETE`` on:

- orders references customers and products; products references suppliers.
- While all four are live, orders lists customers and products as parents,
  and products lists suppliers as a parent and orders as a child.
- Once customers and suppliers are dropped, orders lists products only and
  products lists no parent, and neither dropped table is named anywhere in
  the tool's answer.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_soft_delete_fetch_full_record_fk_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.config.constants.arangodb import RecordRelations
from app.utils.fetch_full_record import create_fetch_full_record_tool
from tests.integration import test_soft_delete_chat_fk_e2e as chat_fk

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

# The same four seeded tables, on both backends.
world = chat_fk.world

_World = chat_fk._World


async def _open(w: _World, *names: str) -> dict[str, dict[str, Any]]:
    """What the agent gets back from fetch_full_record for these tables, by name."""
    retrieved = {
        w.vrid(name): {"id": w.ids[name], "record_type": "SQL_TABLE", "record_name": name}
        for name in names
    }
    tool = create_fetch_full_record_tool(
        retrieved, org_id=w.org_id, graph_provider=w.graph, user_id="agent-user",
    )
    answer = await tool.coroutine(record_ids=[w.ids[name] for name in names])
    assert answer["ok"] is True, answer
    return {w.name_of(record["id"]): record for record in answer["records"]}


def _named(w: _World, relations: list[dict[str, Any]]) -> set[str]:
    return {w.name_of(r["record_id"]) for r in relations}


async def test_the_full_record_tool_lists_live_neighbours(world: _World) -> None:
    opened = await _open(world, "orders", "products")

    assert _named(world, opened["orders"]["fk_parent_record_ids"]) == {"customers", "products"}
    assert _named(world, opened["orders"]["fk_child_record_ids"]) == set()
    assert _named(world, opened["products"]["fk_parent_record_ids"]) == {"suppliers"}
    assert _named(world, opened["products"]["fk_child_record_ids"]) == {"orders"}
    customers = next(
        r for r in opened["orders"]["fk_parent_record_ids"] if r["record_id"] == world.ids["customers"]
    )
    assert customers["parentTable"] == "public.customers"
    assert customers["sourceColumn"] == "customers_id"


async def test_the_full_record_tool_leaves_out_neighbours_in_the_trash(world: _World) -> None:
    for dropped in ("customers", "suppliers"):
        await world.processor.on_record_deleted(world.ids[dropped])
        stored = await world.graph.get_document(world.ids[dropped], chat_fk.RECORDS)
        assert stored is not None and stored.get("isDeleted") is True, f"{dropped} is in the trash"
    edges = await world.graph.get_parent_record_ids_by_relation_type(
        world.ids["orders"], RecordRelations.FOREIGN_KEY.value
    )
    assert world.ids["customers"] in {e["record_id"] for e in edges}, "the trash keeps the foreign key"

    opened = await _open(world, "orders", "products")

    assert _named(world, opened["orders"]["fk_parent_record_ids"]) == {"products"}
    assert _named(world, opened["products"]["fk_parent_record_ids"]) == set()
    assert _named(world, opened["products"]["fk_child_record_ids"]) == {"orders"}
    everything_said = repr(opened)
    for dropped in ("customers", "suppliers"):
        assert world.ids[dropped] not in everything_said
        assert f"public.{dropped}" not in everything_said
        assert f"{dropped}_id" not in everything_said
