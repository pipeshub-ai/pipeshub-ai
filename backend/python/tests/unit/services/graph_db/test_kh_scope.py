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
])
async def test_every_fallback_says_why(m, reason) -> None:
    with pytest.raises(ks.Fallback) as err:
        await page(FakeClient(m), sort_field="name")
    assert err.value.reason.startswith(reason)


@pytest.mark.parametrize("sort_field", ["updatedAt", "createdAt"])
async def test_a_date_sort_is_the_full_querys(sort_field) -> None:
    """The listing shows a source date no stamped property holds."""
    with pytest.raises(ks.Fallback) as err:
        await page(FakeClient(meta()), sort_field=sort_field)
    assert err.value.reason == f"sort {sort_field!r} has no scope index"


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


async def test_reset_leaves_a_flag_marked_after_the_snapshot() -> None:
    """A sync that starts while startup is still reading leases bumps generation.
    The clear only matches the pairs the read saw, so that flag stays."""

    class _Client:
        def __init__(self) -> None:
            self.flags = {"crashed": 2}
            self.cleared: list[str] = []

        async def execute_query(self, query: str, parameters: dict | None = None) -> list[dict]:
            if "RETURN m.connectorId" in query:
                # mark_stale for a sync that started during the await, and a
                # replacement of the crashed row. Neither pair is in the snapshot.
                self.flags["live"] = 1
                self.flags["crashed"] = 3
                return [{"id": "crashed", "generation": 2}]
            if "SET m.syncing = false" in query:
                params = parameters or {}
                guards_generation = "m.generation = row.generation" in query
                for row in params.get("seen") or []:
                    if row["id"] in set(params.get("keep") or []):
                        continue
                    current = self.flags.get(row["id"])
                    if current is None:
                        continue
                    if guards_generation and current != row["generation"]:
                        continue
                    self.cleared.append(row["id"])
                    del self.flags[row["id"]]
                return []
            raise AssertionError(query[:80])

    client = _Client()
    await ks.reset_syncing(client)
    assert client.cleared == []
    assert client.flags == {"crashed": 3, "live": 1}


class _ResetFlags:
    def __init__(self, flags: dict[str, int | None]) -> None:
        self.flags = dict(flags)
        self.cleared: list[str] = []

    async def execute_query(self, query: str, parameters: dict | None = None) -> list[dict]:
        if "RETURN m.connectorId" in query:
            return [{"id": cid, "generation": gen} for cid, gen in self.flags.items()]
        if "SET m.syncing = false" in query:
            params = parameters or {}
            guards_generation = "m.generation = row.generation" in query
            for row in params.get("seen") or []:
                if row["id"] in set(params.get("keep") or []):
                    continue
                if row["id"] not in self.flags:
                    continue
                current = self.flags[row["id"]]
                if guards_generation and current != row["generation"]:
                    continue
                self.cleared.append(row["id"])
                del self.flags[row["id"]]
            return []
        raise AssertionError(query[:80])


async def test_reset_clears_an_unchanged_flag() -> None:
    """A leftover crash flag whose generation did not move is still cleared."""
    client = _ResetFlags({"crashed": 2})
    await ks.reset_syncing(client, keep=())
    assert client.cleared == ["crashed"]
    assert client.flags == {}


async def test_reset_leaves_a_kept_flag() -> None:
    client = _ResetFlags({"live": 4, "crashed": 2})
    await ks.reset_syncing(client, keep={"live"})
    assert client.cleared == ["crashed"]
    assert client.flags == {"live": 4}


async def test_a_restamp_during_the_read_falls_back() -> None:
    client = FakeClient(meta(), rows=[{"id": "a", "sortKey": "a"}], metas_after=[meta(stampedAt=2)])
    with pytest.raises(ks.Fallback, match="restamped"):
        await page(client)
