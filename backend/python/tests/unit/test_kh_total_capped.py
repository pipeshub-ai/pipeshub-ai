"""``totalItems`` that stops at a provider's cap says so (``totalCapped``), on every page of the listing, and an exact total stays as it was."""
from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
import yaml

from app.connectors.sources.localKB.api.knowledge_hub_models import (
    CountsInfo,
    PaginationInfo,
)
from app.connectors.sources.localKB.handlers.kh_search import search_page
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.utils.kh_cursor import Boundary, KnowledgeHubCursor, decode, encode

from .test_kh_search import ORG, SECRET, USER_ID, USER_KEY, FakeProvider, row

SPEC = Path(__file__).resolve().parents[3] / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


class CappedProvider(FakeProvider):
    """The provider's own cap: it counts to ``cap`` and says it stopped."""

    def __init__(self, *a, cap: int, **k) -> None:
        super().__init__(*a, **k)
        self.cap = cap

    async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
        out = await super().get_knowledge_hub_connector_page_v3(app_id=app_id, **kwargs)
        if out["total"] is not None and out["total"] > self.cap:
            out["total"], out["totalCapped"] = self.cap, True
        return out


def _rows(n: int) -> list[dict]:
    return [row(f"r{i:03d}", f"name{i:03d}") for i in range(n)]


def test_a_cursor_carries_the_flag_only_when_it_is_set_and_old_cursors_read_as_exact() -> None:
    boundary = Boundary.of(row("a", "a"))
    capped = encode(KnowledgeHubCursor(boundary=boundary, total=10_000, total_capped=True), SECRET)
    exact = encode(KnowledgeHubCursor(boundary=boundary, total=7), SECRET)
    assert decode(capped, SECRET).total_capped is True and decode(exact, SECRET).total_capped is False
    assert "tk" in KnowledgeHubCursor(boundary=boundary, total=1, total_capped=True).to_wire()
    assert "tk" not in KnowledgeHubCursor(boundary=boundary, total=1).to_wire()          # cursors of an exact total are the bytes they were


def test_a_capped_total_is_flagged_on_the_first_page_and_carried_to_the_next() -> None:
    provider = CappedProvider({"c1": _rows(12)}, cap=5)
    first = asyncio.run(search_page(provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=4))
    assert first.total == 5 and first.total_capped is True and first.next_cursor
    second = asyncio.run(search_page(provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=4, cursor_token=first.next_cursor))
    assert second.total == 5 and second.total_capped is True                           # the total was counted once; so was the cap


def test_an_exact_total_is_not_flagged() -> None:
    page = asyncio.run(search_page(FakeProvider({"c1": _rows(3)}), user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=10))
    assert page.total == 3 and page.total_capped is False


@pytest.fixture
def service() -> KnowledgeHubService:
    import logging
    config = AsyncMock()
    config.get_config.return_value = {"scopedJwtSecret": "test-secret"}
    graph = AsyncMock()
    graph.get_user_by_user_id.return_value = {"_key": "uk1"}
    graph.get_knowledge_hub_access_context_v2.return_value = {"grantee_ids": ["uk1"], "gated_app_ids": ["app1"]}
    graph.get_hidden_app_ids.return_value = []
    return KnowledgeHubService(logger=logging.getLogger("t"), graph_provider=graph, config_service=config)


def _envelope(total: int, capped: bool | None) -> dict:
    part = {"partitionId": "p1", "partitionKind": "BROWSE", "appId": None, "rows": [{"id": "app1", "name": "A", "nodeType": "app", "sortKey": "a", "nullRank": 0,
                                                                                       "origin": "CONNECTOR", "createdAt": 1, "updatedAt": 2, "hasChildren": False}],
            "hasMore": True, "exhausted": False, "total": total, "ids": [], "countsByType": {"app": total}}
    if capped is not None:
        part["totalCapped"] = capped
    return {"partitions": [part], "scope": None}


@pytest.mark.asyncio
@pytest.mark.parametrize(("capped", "want"), [(True, True), (False, False), (None, False)])
async def test_the_response_says_whether_the_total_is_a_lower_bound(service, capped, want) -> None:
    service.graph_provider.get_knowledge_hub_root_nodes_v2.return_value = _envelope(10_000, capped)
    out = await service.get_nodes(user_id="u1", org_id="o1", limit=200, include=["counts"])
    p = out.pagination
    assert p.totalItems == 10_000 and p.totalCapped is want
    assert p.totalPages == 50                                                             # still an integer, still ceil(totalItems / limit)
    assert out.counts.total == 10_000 and out.counts.capped is want


def test_the_models_default_to_exact_and_the_spec_documents_the_new_fields() -> None:
    assert PaginationInfo(limit=1, totalItems=0, hasNext=False, hasPrev=False).totalCapped is False
    assert CountsInfo(items=[], total=0).capped is False
    schemas = yaml.safe_load(SPEC.read_text(encoding="utf-8"))["components"]["schemas"]
    pagination = schemas["KnowledgeHubNodesResponse"]["properties"]["pagination"]
    assert pagination["properties"]["totalCapped"]["type"] == "boolean" and "totalCapped" not in pagination["required"]
    counts = schemas["KnowledgeHubNodesResponse"]["properties"]["counts"]["properties"]
    assert counts["capped"]["type"] == "boolean" and set(counts) >= {"items", "total", "capped"}
