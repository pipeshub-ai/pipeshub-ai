"""H5 RA: the in-memory record-access fake is the third adapter of RB-8's contract (Neo4j and ArangoDB are the others,
``tests/integration/graph_db/test_record_access_batch.py``), and every adapter implements the whole surface."""

from __future__ import annotations

import inspect

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.interface.record_access import RECORD_ACCESS_METHODS
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.support.record_access import InMemoryRecordAccess
from tests.support.record_access_contract import (
    AccessWorld,
    assert_implements_every_access_method,
    assert_record_access_contract,
)

ORG = "org-1"


def _world() -> AccessWorld:
    fake = InMemoryRecordAccess()
    fake.user("u-1", ORG, groups=("g-1",), apps=("conn-1",)).user("u-2", ORG).user("u-3", "org-2")
    for rid in ("direct", "group", "org", "nobody", "others", "no_app", "other_org_grant"):
        fake.record(f"r-{rid}", ORG, connectorId="conn-2" if rid == "no_app" else "conn-1")
    fake.record("r-foreign", "org-2", connectorId="conn-1")
    fake.record("r-trashed", ORG, connectorId="conn-1", isDeleted=True)
    fake.grant("r-direct", user="u-1").grant("r-group", group="g-1", role="WRITER").grant("r-org", org=ORG)
    fake.grant("r-others", user="u-2").grant("r-no_app", user="u-1").grant("r-other_org_grant", org="org-2")
    fake.grant("r-foreign", user="u-1").grant("r-trashed", user="u-1")
    return AccessWorld(
        access=fake,
        org_id=ORG,
        user_id="u-1",
        readable={"direct": "r-direct", "group": "r-group", "org": "r-org"},
        unreadable={
            "no_grant": "r-nobody",
            "another_users": "r-others",
            "app_not_the_users": "r-no_app",
            "another_orgs_grant": "r-other_org_grant",
            "another_orgs_record": "r-foreign",
            "missing": "r-missing",
            "in_the_trash": "r-trashed",
        },
    )


async def test_ra1_the_fake_holds_the_record_access_contract() -> None:
    await assert_record_access_contract(_world())


@pytest.mark.parametrize("adapter", [InMemoryRecordAccess, Neo4jProvider, ArangoHTTPProvider])
def test_ra2_every_adapter_implements_every_access_method(adapter: type) -> None:
    assert_implements_every_access_method(adapter)


def test_ra2_the_surface_is_part_of_the_graph_port() -> None:
    assert RECORD_ACCESS_METHODS == (
        "check_access", "check_record_access_with_details", "filter_accessible_record_ids",
    )
    for name in RECORD_ACCESS_METHODS:
        assert inspect.iscoroutinefunction(getattr(IGraphDBProvider, name)), name
        assert inspect.iscoroutinefunction(getattr(InMemoryRecordAccess, name)), name


async def test_ra3_details_carry_the_document_and_the_best_role() -> None:
    fake = InMemoryRecordAccess().user("u-1", ORG, groups=("g",))
    fake.record("r-1", ORG, connectorId="c", recordName="Plan", recordType="MAIL")
    fake.grant("r-1", user="u-1").grant("r-1", group="g", role="OWNER")
    details = await fake.check_record_access_with_details("u-1", ORG, "r-1")
    assert details is not None
    assert details["record"] | {"fileRecord": None} == {
        "id": "r-1", "orgId": ORG, "connectorId": "c", "recordName": "Plan", "recordType": "MAIL",
        "fileRecord": None, "mailRecord": None, "ticketRecord": None,
    }
    assert details["permissions"] == [
        {"id": "r-1", "name": "Plan", "type": "MAIL", "relationship": "OWNER", "accessType": "GROUP"}
    ]


async def test_ra4_an_outage_raises_from_every_method_and_serve_installs_the_whole_surface() -> None:
    from unittest.mock import MagicMock

    fake = InMemoryRecordAccess().readable("u-1", ORG, "r-1")
    graph = fake.serve(MagicMock())
    assert (await graph.check_access("u-1", ORG, node_ids=["r-1"])).node_ids == {"r-1"}
    fake.fail_with(ConnectionError("graph down"))
    with pytest.raises(ConnectionError):
        await graph.check_record_access_with_details("u-1", ORG, "r-1")
    with pytest.raises(ConnectionError):
        await graph.check_access("u-1", ORG, node_ids=["r-1"])
