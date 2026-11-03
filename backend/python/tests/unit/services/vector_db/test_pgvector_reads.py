"""Pure parts of the pgvector read path (scroll cursors, payload shaping, which points a scope admits) and the provider's read methods."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import org_context
from app.services.vector_db.models import FieldCondition, FilterExpression
from app.services.vector_db.pgvector import reads
from app.services.vector_db.pgvector.catalog import CollectionRow, OrgSpace
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionNotFoundError,
    OrgMismatch,
    OrgRequired,
    PgVectorUnsupportedError,
)
from app.services.vector_db.pgvector.pgvector import PgVectorService
from app.services.vector_db.pgvector.visibility import Scope, scope_of

A, B = "a" * 24, "b" * 24
ROW = CollectionRow(7, "records", True, True, "v", "ready")


def cond(key: str, value: object = None, values: list | None = None, lte: int | None = None) -> FieldCondition:
    return FieldCondition(key=key, value=value, values=values, values_count_lte=lte)


# ---- scroll cursors and payload shaping ------------------------------------------------------------------------------------------
def test_a_scroll_offset_belongs_to_its_org() -> None:
    off = reads.encode_offset(A, 12345)
    assert reads.decode_offset(A, off) == 12345
    with pytest.raises(OrgMismatch):
        reads.decode_offset(B, off)
    for bad in ("", "AAAA", "!!!", "x" * 40):
        with pytest.raises(ValueError, match="offset"):
            reads.decode_offset(A, bad)


def row(**kw: object) -> dict:
    base = {"point_id": uuid.UUID(int=1), "payload": json.dumps({"metadata": {"orgId": A, "virtualRecordId": "r1", "x": 1}}),
            "page_content": "text", "connector_ids": ["c"], "record_group_ids": None, "root_record_group_ids": [], "vrid": "r1",
            "block_id": "b", "ent_type": None, "ent_id": None, "ent_kind": None, "ent_level": None}
    return {**base, **kw}


def test_a_point_comes_back_with_the_payload_the_other_providers_return() -> None:
    p = reads.to_point(row(), None, A)
    assert p.id == str(uuid.UUID(int=1)) and p.payload["page_content"] == "text"
    assert p.payload["metadata"] == {"orgId": A, "virtualRecordId": "r1", "x": 1}
    assert (p.payload["connectorIds"], p.payload["recordGroupIds"], p.payload["rootRecordGroupIds"]) == (["c"], [], [])      # absent: empty, not null
    assert reads.to_point(row(page_content=None), None, A).payload["page_content"] == ""


def test_only_the_requested_paths_are_returned_and_column_paths_need_no_payload() -> None:
    p = reads.to_point(row(), ["metadata.x", "page_content", "metadata.missing"], A)
    assert p.payload == {"metadata": {"x": 1}, "page_content": "text"}
    cols = reads.to_point(row(payload=None, page_content=None), ["metadata.virtualRecordId", "metadata.blockId", "connectorIds"], A)
    assert cols.payload == {"metadata": {"virtualRecordId": "r1", "blockId": "b"}, "connectorIds": ["c"]}
    assert reads._needs(["metadata.virtualRecordId", "connectorIds"]) == (False, False)
    assert reads._needs(["metadata.x"]) == (True, False) and reads._needs(["page_content"]) == (False, True) and reads._needs(None) == (True, True)


# ---- which points a scope admits ---------------------------------------------------------------------------------------------------
def test_a_filter_becomes_a_scope() -> None:
    expr = FilterExpression(must=[cond("metadata.orgId", A), cond("metadata.virtualRecordId", values=["r1", "r2"]), cond("metadata.kind", "k")],
                            should=[cond("connectorIds", values=["c1"]), cond("recordGroupIds", values=["g1"]), cond("rootRecordGroupIds", values=["x1"]),
                                    cond("metadata.virtualRecordId", values=["r9"])],
                            must_not=[cond("metadata.level", "deep")])
    s = scope_of(expr)
    assert (s.apps, s.groups, s.roots, s.direct, s.must_vrids) == (["c1"], ["g1"], ["x1"], ["r9"], ["r1", "r2"]) and s.extra is not None
    assert not s.whole_org
    assert scope_of(FilterExpression(must=[cond("metadata.orgId", A)])) == Scope()
    two = scope_of(FilterExpression(must=[cond("metadata.virtualRecordId", values=["r1", "r2"]), cond("metadata.virtualRecordId", values=["r2", "r3"])]))
    assert two.must_vrids == ["r2"]                                                  # two narrowing lists intersect


def test_an_empty_accessible_list_matches_nothing_instead_of_everything() -> None:
    s = scope_of(FilterExpression(must=[cond("metadata.orgId", A)], should=[cond("connectorIds", values=[]), cond("recordGroupIds", values=[])]))
    assert (s.apps, s.groups, s.roots, s.direct) == ([], [], [], []) and not s.whole_org      # all four arms present, all empty
    assert "p.group_key IN (" in s.candidates() and "EXISTS" in s.probe()                       # the membership test is still applied


@pytest.mark.parametrize("expr", [
    FilterExpression(must=[cond("metadata.other", "x")]),
    FilterExpression(must=[cond("connectorIds", values=["c"])]),
    FilterExpression(should=[cond("metadata.kind", "k")]),
    FilterExpression(should=[cond("connectorIds", lte=2)]),
    FilterExpression(must_not=[cond("connectorIds", values=["c"])]),
    FilterExpression(should=[cond("connectorIds", values=["c"])], min_should_match=2),
], ids=["payload key in must", "membership in must", "column in should", "count in should", "membership in must_not", "min_should_match"])
def test_what_a_search_cannot_express_is_refused_not_dropped(expr: FilterExpression) -> None:
    with pytest.raises(PgVectorUnsupportedError):
        scope_of(expr)


def test_the_filter_fragments_come_from_one_scope() -> None:
    whole = Scope()
    assert whole.candidates() == whole.probe() == "" and Scope(must_vrids=["r"]).probe() == " AND p.group_key = ANY($9::text[])"
    narrow = Scope(["c"], [], [], [], ["r"])
    assert narrow.args() == (["c"], [], [], [], ["r"]) and "ANY($9::text[])" in narrow.candidates()
    assert Scope(["c"]).key() == Scope(["c"]).key() != Scope(["d"]).key()
    sql, params = scope_of(FilterExpression(must=[cond("metadata.kind", "k")])).extra_sql()
    assert sql.startswith(" AND (") and params == [["k"]] and "$12" in sql


def test_a_scope_that_lists_every_connector_drops_its_arms_only_when_that_is_provably_the_same_set() -> None:
    every = frozenset({"c0", "c1"})
    assert Scope(["c0", "c1", "c9"], ["g"], [], ["r"], ["m"]).narrowed_to(every) == Scope(None, None, None, None, ["m"])
    assert Scope([], ["g1"], [], []).narrowed_to(every) == Scope([], ["g1"], [], [])             # no connector arm: unchanged
    assert Scope(["c0"], [], [], []).narrowed_to(every).apps == ["c0"]                             # not every connector
    assert Scope(["c0", "c1"], [], [], []).narrowed_to(frozenset()) == Scope(["c0", "c1"], [], [], [])   # a collection without connectors
    assert Scope(["c0", "c1"], [], [], []).narrowed_to(None) == Scope(["c0", "c1"], [], [], [])       # not proven
    assert Scope([], [], [], []).narrowed_to(frozenset()) == Scope([], [], [], [])                  # matches nothing stays nothing


# ---- the provider's read methods ---------------------------------------------------------------------------------------------------
@pytest.fixture
async def svc(monkeypatch) -> PgVectorService:
    s = PgVectorService(PgVectorConfig(dsn="postgresql://h/db"))
    s._db = SimpleNamespace(run=AsyncMock(), capabilities=None)
    s._cfg = s.config_service
    s._catalog = MagicMock()
    s._catalog.require_collection = AsyncMock(return_value=ROW)
    s._catalog.active_space = AsyncMock(return_value=OrgSpace(5, "active", "p:m:4", 4, "cosine"))
    return s


def pinned(org: str = A, **should: list) -> FilterExpression:
    return FilterExpression(must=[cond("metadata.orgId", org)], should=[cond(k, values=v) for k, v in should.items()])


@pytest.mark.asyncio
async def test_scroll_needs_a_pinned_org_a_positive_limit_and_a_collection(svc) -> None:
    for bad in (FilterExpression(), FilterExpression(must=[cond("metadata.virtualRecordId", "r1")]), pinned("not-an-org"),
                FilterExpression(must=[cond("metadata.orgId", A), cond("metadata.orgId", B)])):
        with pytest.raises(OrgRequired):
            await svc.scroll("records", bad, 10)
    with pytest.raises(ValueError, match="limit"):
        await svc.scroll("records", pinned(), 0)
    svc._catalog.require_collection = AsyncMock(side_effect=CollectionNotFoundError("nope"))
    with pytest.raises(CollectionNotFoundError):
        await svc.scroll("nope", pinned(), 10)
    svc._db.run.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_org_without_a_space_has_no_points_and_a_page_is_what_the_read_returned(svc) -> None:
    svc._catalog.active_space = AsyncMock(return_value=None)
    page = await svc.scroll("records", pinned(), 10)
    assert page.points == [] and page.next_offset is None
    svc._db.run.assert_not_awaited()
    svc._catalog.active_space = AsyncMock(return_value=OrgSpace(5, "active", "p:m:4", 4, "cosine"))
    svc._db.run = AsyncMock(return_value=(["p"], "next"))
    page = await svc.scroll("records", pinned(connectorIds=[]), 10, "off", ["metadata.x"])
    assert (page.points, page.next_offset) == (["p"], "next")


@pytest.mark.asyncio
async def test_retrieve_uses_the_requests_org_keeps_the_order_asked_and_skips_what_is_not_an_id(svc) -> None:
    ids = [str(uuid.UUID(int=n)) for n in (3, 1, 2)]
    with pytest.raises(OrgRequired):
        await svc.retrieve_points("records", ids)
    found = [SimpleNamespace(id=str(uuid.UUID(int=n))) for n in (1, 2, 3)]
    svc._db.run = AsyncMock(return_value=found)
    with org_context.bind(org_context.OrgContext(A, None, "service", "t", "-")):
        got = await svc.retrieve_points("records", [*ids, "not-a-uuid"])
        assert [p.id for p in got] == ids
        svc._db.run.reset_mock()
        assert await svc.retrieve_points("records", ["not-a-uuid"]) == [] and await svc.retrieve_points("records", []) == []
        svc._db.run.assert_not_awaited()
        svc._catalog.active_space = AsyncMock(return_value=None)
        assert await svc.retrieve_points("records", ids) == []
