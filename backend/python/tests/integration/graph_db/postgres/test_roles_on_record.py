"""``DataSourceEntitiesProcessor.get_roles_on_record`` on PostgreSQL, against a real database: the role on each user's
and group's own permission edge into a record, and removing a user's and a Person's. Neo4j and ArangoDB are in ``../test_roles_on_record_real_backends.py``.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock

import pytest

from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.models.entities import Person

from .test_access import APP, World, rec
from .test_access import w as w

pytestmark = pytest.mark.integration


async def test_roles_are_those_of_each_principals_own_edge_into_the_record(w: World) -> None:
    await w.nodes("users", {"_key": "writer", "email": "Writer@example.com"}, {"_key": "inherited", "email": "inherited@example.com"})
    await w.nodes("groups", {"_key": "grp", "name": "Eng", "externalGroupId": "eng@example.com", "connectorName": "DRIVE WORKSPACE",
                             "connectorId": APP, "createdAtTimestamp": 1, "updatedAtTimestamp": 1})
    await w.nodes("recordGroups", {"_key": "rg", "connectorId": APP})
    await w.nodes("records", rec("r"))
    await w.child(("recordGroups", "rg"), ("records", "r"))
    await w.edge("permission", ("users", "writer"), ("records", "r"), type="USER", role="WRITER")
    await w.edge("permission", ("groups", "grp"), ("records", "r"), type="GROUP", role="READER")
    await w.edge("permission", ("users", "inherited"), ("recordGroups", "rg"), type="USER", role="READER")
    await w.p.batch_upsert_people([Person(id="partner", org_id=w.org, email="Partner@example.org")])    # an external collaborator
    await w.edge("permission", ("person", "partner"), ("records", "r"), type="USER", role="READER")
    log = logging.getLogger("roles-on-record-pg")
    processor = DataSourceEntitiesProcessor(log, GraphDataStore(log, w.p), AsyncMock())
    processor.org_id = w.org

    users, groups = await processor.get_roles_on_record("r")

    assert users == {"writer@example.com": "WRITER", "partner@example.org": "READER"}, "a user reaching it through the record group has no own edge"
    assert groups == {"eng@example.com": "READER"}

    for email in ("Writer@example.com", "Partner@example.org"):
        await processor.delete_principal_permission_from_record("r", email, APP)

    assert await processor.get_roles_on_record("r") == ({}, {"eng@example.com": "READER"})
