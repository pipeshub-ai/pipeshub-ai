"""Byte-identical files stay visible to every tool (A-7).

Identical content shares a virtualRecordId, and the bulk accessible-set lookup
keeps one record per virtualRecordId. Gating the tools on that map hid every
copy but one -- forks, vendored copies, a helper duplicated across services --
while results still said `truncated: false`.
"""
import pytest

from app.agents.actions.code_graph.neighbours import get_neighbour_impl
from app.agents.actions.code_graph.query import query_code_graph_impl
from app.agents.actions.code_graph.read import read_code_impl

from .conftest import BLOCKS, CONN, ORG, USER, FakeBlobStore, QueryGraphProvider, _block

pytestmark = pytest.mark.asyncio

COPY = "vendor/b_copy.py"


class TwinGraph(QueryGraphProvider):
    """`src/b.py` and a byte-identical copy, under one content identity."""

    def __init__(self) -> None:
        super().__init__()
        self.blocks["k_twin"] = _block("k_twin", "rec-e", COPY, "vendor_copy_target", "target")
        self.blocks["k_twin"].pop("filePath")
        self.code_files["rec-e"] = COPY
        self.records["rec-e"] = {"_key": "rec-e", "orgId": ORG, "connectorId": CONN,
                                 "virtualRecordId": self.records["rec-b"]["virtualRecordId"]}
        self.edges.append({"_from": f"{BLOCKS}/k_caller", "_to": f"{BLOCKS}/k_twin",
                           "relationshipType": "CALLS"})
        for block in self.blocks.values():
            block["connectorId"] = CONN

    async def get_accessible_virtual_record_ids(self, user_id, org_id, **kwargs) -> dict:
        # What production returns: one record per content identity.
        return {rec["virtualRecordId"]: rid for rid, rec in reversed(self.records.items())}


async def test_both_copies_can_be_read() -> None:
    graph = TwinGraph()
    for path in ("src/b.py", COPY):
        out = await read_code_impl(
            graph_provider=graph, connector_id=CONN, blob_store=FakeBlobStore(),
            org_id=ORG, user_id=USER, file_path=path,
        )
        assert "error" not in out, path


async def test_a_walk_reaches_both_copies() -> None:
    out = await get_neighbour_impl(
        graph_provider=TwinGraph(), connector_id=CONN, org_id=ORG, user_id=USER,
        file_path="src/a.py", qualified_name="function:caller", direction="outbound",
        edge_types=["CALLS"],
    )
    assert {r["file_path"] for r in out["neighbors"]} == {"src/b.py", COPY}


async def test_a_listing_shows_both_copies() -> None:
    out = await query_code_graph_impl(graph_provider=TwinGraph(), org_id=ORG, user_id=USER,
                                      connector_id=CONN, select=COPY)
    assert out["matches"] == 1
