"""``TopGrants``: the grants a PostgreSQL browse hands the chain-top walk. From kg's marks they are those of the asked record groups and of no known group, granted ids only;
without kg's answer they are every grant, as the walk takes them."""
from __future__ import annotations

from typing import Any

from app.services.graph_db.postgres.kh_listing import TopGrants

IDS = {1: "a", 2: "b", 3: "c", 4: "d", 5: "e"}


class _Tx:
    def __init__(self) -> None:
        self.asked: list[tuple] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict]:  # noqa: ANN401
        self.asked.append(args)
        org, groups, ids = args
        return [{"sk": g} for g in groups if g == 70 and "e" in ids]      # e is in group 70 (by a link)


def _top(by_group: dict | None, granted: set[str], loose: dict | None = None) -> tuple[TopGrants, list[list[int]]]:
    read: list[list[int]] = []

    async def keys(sks: list[int]) -> dict[int, str]:
        read.append(sorted(sks))
        return {s: IDS[s] for s in sks}
    return TopGrants(granted, by_group, loose or {}, keys), read


async def test_from_the_marks_the_walk_gets_the_asked_groups_and_those_of_no_group_and_only_granted_ids() -> None:
    top, read = _top({10: [1, 2], 20: [3], None: [5]}, {"a", "b", "c", "e"}, {5: "e"})
    assert top.groups == {10, 20}
    assert await top.ids({20}) == ["c", "e"]
    assert await top.ids(set()) == ["e"]
    assert await top.ids() == ["a", "b", "c", "e"]
    assert read == [[3], [1, 2]]                       # each id read once, the loose ones never
    top, _ = _top({10: [1, 4]}, {"a"})
    assert await top.ids({10}) == ["a"]                # d is no grant of the viewer's (any more): never handed on


async def test_without_kg_every_grant_goes_to_the_walk_and_held_asks_with_every_grant() -> None:
    top, read = _top(None, {"b", "a"})
    assert top.groups == set() and await top.ids({10}) == ["a", "b"] and read == []
    tx = _Tx()
    assert await top.held(tx, "o", [70, 71]) == set() and tx.asked == [("o", [70, 71], ["a", "b"])]


async def test_held_is_every_asked_group_of_a_grant_and_those_the_loose_ones_are_in() -> None:
    top, _ = _top({70: [1], 80: [2], None: [5]}, {"a", "b", "e"}, {5: "e"})
    tx = _Tx()
    assert await top.held(tx, "o", [70, 71, 80]) == {70, 80}
    assert tx.asked == [("o", [70, 71, 80], ["e"])]
    top, _ = _top({80: [2]}, {"b"})
    tx = _Tx()
    assert await top.held(tx, "o", [70]) == set() and tx.asked == []      # nothing of no group: no read


class _KgTx:
    """kg's read unit for one browse: the connector's row, whether the viewer is linked to a source account in it, and kg's candidate grants."""
    def __init__(self, linked: bool) -> None:
        self.linked, self.candidates_read = linked, False

    async def fetchval(self, sql: str, *args: Any) -> Any:  # noqa: ANN401
        from app.services.graph_db.postgres import kh_listing
        if sql == kh_listing.APP_DOC:
            return {"type": "DRIVE"}
        assert sql == kh_listing.LINKED_ACCOUNT and args == ("o", "u1", "app1"), (sql, args)
        return self.linked

    async def fetch(self, sql: str, *args: Any) -> list[dict]:  # noqa: ANN401
        self.candidates_read = True
        return [{"node_sk": 1, "node_group": None, "node_parent": None}] if "chain_top_grants" in sql else [{"sk": 1, "id": "a"}]


async def _top_grants(monkeypatch: Any, linked: bool) -> tuple[TopGrants, _KgTx]:  # noqa: ANN401
    from app.services.graph_db.postgres import kh_listing
    from app.services.graph_db.postgres.kg import hierarchy

    tx = _KgTx(linked)

    async def answer(provider: Any, what: str, transaction: Any, fn: Any) -> Any:  # noqa: ANN401
        try:
            return await fn(tx)
        except hierarchy.Fallback:
            return None

    async def served(tx: Any, org: str, app_id: str) -> int:  # noqa: ANN401
        return 9
    monkeypatch.setattr(hierarchy, "answer", answer)
    monkeypatch.setattr(hierarchy, "_served", served)
    provider = kh_listing.KhListingMixin.__new__(kh_listing.KhListingMixin)
    return await provider._kh_top_grants("o", "app1", "u1", {"a", "z"}, None), tx


async def test_a_viewer_linked_to_a_source_account_hands_the_walk_every_grant(monkeypatch) -> None:
    """kg keys no source account, so its grants are not among kg's candidates: a viewer linked to one in the connector gets every grant, or the
    record only that account's grant reaches is never placed as a chain-top and the browse leaves it out."""
    top, tx = await _top_grants(monkeypatch, linked=True)
    assert top.by_group is None and top.parents == {} and await top.ids() == ["a", "z"] and not tx.candidates_read
    top, tx = await _top_grants(monkeypatch, linked=False)
    assert top.by_group == {None: [1]} and await top.ids() == ["a"] and tx.candidates_read
