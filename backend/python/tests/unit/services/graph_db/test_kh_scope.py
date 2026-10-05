"""The knowledge hub scope module's pure parts and its decisions per request, without a database."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.graph_db.neo4j import kh_scope as ks

APP = "app"


def node(label: str = "Record", *, parent: str | None = APP, inherits: bool = True, rule: str = "OPEN",
         deleted: bool = False, hides: bool = False, pm: str | None = None, origin: str | None = None) -> tuple:
    return (label, deleted, hides, rule, pm, False, False, True, False, parent, 1 if parent else 0, inherits,
            None, False, False, origin)


APP_NODE = ("App", False, False, "OPEN", None, False, False, True, False, None, 0, False, None, False, False, None)


def test_hop_classification() -> None:
    p = node("RecordGroup")
    assert ks._classify(node(), p) == "free"
    assert ks._classify(node(rule="STRICT"), p) == "free"
    assert ks._classify(node(inherits=False), p) == "gate"
    assert ks._classify(node(rule="RESTRICTED"), p) == "gate"
    assert ks._classify(node(rule="RESTRICTED", inherits=False), p) == "block"
    assert ks._classify(node(deleted=True), p) == "dead"
    assert ks._classify(node(), node("RecordGroup", hides=True)) == "dead"
    assert ks._classify(node(), node("RecordGroup", pm="RECORD_GROUP_LEVEL")) == "dead"


def test_scopes_stop_at_the_walks_hop_limit() -> None:
    nodes, parent = {}, APP
    for i in range(60):
        nodes[f"n{i}"] = node(parent=parent)
        parent = f"n{i}"
    scope, _ = ks.compute_scopes(nodes, APP, APP_NODE)
    # Hops 1..50 from the App.
    assert sorted(k for k in scope if k != APP) == sorted(f"n{i}" for i in range(ks.MAX_DEPTH))


def test_gates_open_only_with_a_granted_chain() -> None:
    nodes = {
        "free": node(),
        "gate": node(inherits=False),
        "in-gate": node(parent="gate"),
        "nested": node(parent="in-gate", inherits=False),
        "in-nested": node(parent="nested"),
    }
    scope, scope_parent = ks.compute_scopes(nodes, APP, APP_NODE)
    assert scope == {APP: APP, "free": APP, "gate": "gate", "in-gate": "gate", "nested": "nested",
                     "in-nested": "nested"}
    tree = ks.ScopeTree([{"gate": g, "parent": p, "nRecord": 1, "nFolder": 0, "nGroup": 0}
                         for g, p in scope_parent.items()])
    assert sorted(tree.open_scopes(APP, set())) == [APP]
    assert sorted(tree.open_scopes(APP, {"gate"})) == [APP, "gate"]
    # A nested gate opens only below an open one.
    assert sorted(tree.open_scopes(APP, {"nested"})) == [APP]
    assert sorted(tree.open_scopes(APP, {"gate", "nested", "unrelated"})) == [APP, "gate", "nested"]


def test_an_empty_origin_is_not_defaulted() -> None:
    assert ks.origin_of(node(origin=None)) == "CONNECTOR"
    assert ks.origin_of(node(origin="")) == ""
    assert ks.origin_of(node(origin="UPLOAD")) == "UPLOAD"


class FakeClient:
    """Answers the page_payload queries from canned data; records what ran."""

    def __init__(
        self, meta: dict | None, *, rows: list[dict] | None = None, metas_after: list[dict] | None = None,
    ) -> None:
        self.metas = [meta] + (metas_after or [meta])
        self.rows = rows or []
        self.ran: list[str] = []

    async def execute_query(self, query: str, parameters: dict | None = None) -> list[dict]:
        if "RETURN m.fresh AS fresh" in query:
            meta = self.metas.pop(0) if len(self.metas) > 1 else self.metas[0]
            return [meta] if meta else []
        if "MATCH (k:KhScope {connectorId: $c})" in query:
            return [{"gate": APP, "parent": None, "nRecord": len(self.rows), "nFolder": 0, "nGroup": 0}]
        if "UNWIND $granted" in query:
            return []
        if "USING INDEX n:RecordGroup" in query:
            return []
        if "USING INDEX n:Record(" in query:
            self.ran.append("keyset" if "$ks" in query else "no-keyset")
            return list(self.rows)
        raise AssertionError(f"unexpected query: {query[:80]}")


def meta(**overrides: Any) -> dict:  # noqa: ANN401
    base = {"fresh": True, "eligible": True, "syncing": False, "stampedAt": 1, "generation": 3,
            "version": ks.STAMP_VERSION, "reason": None, "nullNames": 0, "nullUpdated": 0, "nullCreated": 0,
            "origins": ["CONNECTOR"]}
    return {**base, **overrides}


async def page(client: FakeClient, **kwargs: Any) -> dict:  # noqa: ANN401
    args = {"after": None, "include_total": True, **kwargs}
    return await ks.page_payload(client, ks.ScopeCache(), APP, "org", [], 10, args.pop("after"), **args)


@pytest.mark.parametrize("m,reason", [
    (None, "never stamped"),
    (meta(syncing=True), "a sync is running"),
    (meta(fresh=False), "stale since the last write"),
    (meta(version=1), "stamped by version 1"),
    (meta(eligible=False, reason="a node has several parents"), "ineligible: a node has several parents"),
    (meta(nullUpdated=2), "2 node(s) without a numeric updatedAt"),
])
async def test_every_fallback_says_why(m, reason) -> None:
    with pytest.raises(ks.Fallback) as err:
        await page(FakeClient(m), sort_field="updatedAt")
    assert err.value.reason.startswith(reason)


async def test_a_sort_without_an_index_falls_back() -> None:
    with pytest.raises(ks.Fallback):
        await page(FakeClient(meta()), sort_field="sizeInBytes")


async def test_previous_page_with_null_names_falls_back() -> None:
    with pytest.raises(ks.Fallback, match="null names"):
        await page(FakeClient(meta(nullNames=1)), direction="prev", after={"id": "x", "sortKey": "x", "nullRank": 0})


async def test_previous_page_from_the_null_bucket_lists_the_last_named_rows() -> None:
    """Another connector's null names put an All records cursor in the null bucket; this connector has none,
    so going back gives its last named rows, read without a keyset."""
    client = FakeClient(meta(), rows=[{"id": "b", "sortKey": "b"}, {"id": "a", "sortKey": "a"}])
    out = await page(client, direction="prev", after={"id": "zz", "sortKey": None, "nullRank": 1})
    assert [r["id"] for r in out["page"]] == ["b", "a"]
    assert "no-keyset" in client.ran


async def test_forward_from_the_null_bucket_lists_nothing_here() -> None:
    client = FakeClient(meta(), rows=[{"id": "a", "sortKey": "a"}])
    out = await page(client, after={"id": "zz", "sortKey": None, "nullRank": 1})
    assert out["page"] == []
    assert client.ran == []


async def test_an_origin_filter_that_splits_a_connector_falls_back() -> None:
    with pytest.raises(ks.Fallback, match="splits"):
        await page(FakeClient(meta(origins=["CONNECTOR", "UPLOAD"])), origins=["CONNECTOR"])
    out = await page(FakeClient(meta()), origins=["UPLOAD"])
    assert out["path"] == "excluded" and out["page"] == [] and out["total"] == 0


async def test_a_restamp_during_the_read_falls_back() -> None:
    client = FakeClient(meta(), rows=[{"id": "a", "sortKey": "a"}], metas_after=[meta(stampedAt=2)])
    with pytest.raises(ks.Fallback, match="restamped"):
        await page(client)
