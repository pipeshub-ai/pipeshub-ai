"""Removing users' own grants from many records, through the processor, on PostgreSQL.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore

from .test_access import APP, World, rec
from .test_identity import member, w  # noqa: F401  (the fixture)

pytestmark = pytest.mark.integration


async def test_a_users_own_grants_go_from_many_records_and_nothing_else_does(w: World) -> None:  # noqa: F811
    await member(w, "ua", userId="ua", email="ann@x.com")
    await member(w, "ub", userId="ub", email="bo@x.com")
    await w.nodes("groups", {"_key": "g"})
    for key in ("r1", "r2", "r3"):
        await w.nodes("records", rec(key))
        for who in (("users", "ua"), ("users", "ub"), ("groups", "g")):
            await w.edge("permission", who, ("records", key), type="USER" if who[0] == "users" else "GROUP", role="READER")
    log = logging.getLogger("t")
    processor = DataSourceEntitiesProcessor(log, GraphDataStore(log, w.p), MagicMock())
    processor.org_id = w.org
    processor.messaging_producer = MagicMock(send_message=AsyncMock(return_value=True), send_messages=AsyncMock(return_value=[]))

    removed = await processor.remove_user_grants_from_records(["r1", "r2", "r3"], {"bo@x.com"}, APP, batch=2)

    assert removed == 3
    for key in ("r1", "r2", "r3"):
        sources = {(e["from_collection"], e["from_id"]) for e in await w.p.get_edges_to_node(f"records/{key}", "permission")}
        assert sources == {("users", "ua"), ("groups", "g")}
