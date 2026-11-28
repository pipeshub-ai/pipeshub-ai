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
