"""``kg.writers``: every call is chunked at the writers' cap, runs in the caller's unit, and sends its keys in order."""
from __future__ import annotations

import pytest

from app.services.graph_db.postgres.kg.writers import CAP, RECORD_COLS, KgWriter


class FakeTx:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []

    async def fetchval(self, sql: str, *args: object) -> int:
        self.calls.append((sql, args))
        return len(args[0])

    async def fetch(self, sql: str, *args: object) -> list:
        self.calls.append((sql, args))
        return [{"o_coll": c, "o_id": i, "o_sk": n} for n, (c, i) in enumerate(zip(args[0], args[1], strict=True))]


def row(sk: int) -> dict:
    return {c: (sk if c in ("sk", "app_sk") else 3 if c in ("coll", "kind") else False if c in ("deleted", "placeholder", "granted") else "x") for c in RECORD_COLS}


@pytest.mark.asyncio
async def test_a_call_over_the_cap_is_split_and_the_counts_add_up() -> None:
    tx = FakeTx()
    n = await KgWriter(tx).records_upsert([row(i) for i in range(2 * CAP + 7)])
    assert n == 2 * CAP + 7 and [len(c[1][0]) for c in tx.calls] == [CAP, CAP, 7]
    assert all("kg.w_records_upsert" in sql and len(args) == len(RECORD_COLS) for sql, args in tx.calls)


@pytest.mark.asyncio
async def test_keys_are_sent_in_order_and_a_link_named_twice_by_the_caller_is_sent_as_given() -> None:
    tx = FakeTx()
    w = KgWriter(tx)
    await w.links_set([(3, 1, 9), (1, 2, 4), (1, 1, 4)])
    assert tx.calls[0][1] == ([1, 1, 3], [1, 2, 1], [4, 4, 9])
    await w.grants_set([(2, 5, "USER", "READER", 1, True), (1, 5, "USER", None, None, False)])
    assert tx.calls[1][1][0] == [1, 2]
    await w.closure_set([(2, 1, 0, "self"), (1, 1, 0, "self")])
    assert tx.calls[2][1][0] == [1, 2]


@pytest.mark.asyncio
async def test_node_keys_go_out_sorted_by_collection_code_and_id_and_come_back_by_key() -> None:
    tx = FakeTx()
    got = await KgWriter(tx).nodes_upsert({("records", "b"), ("apps", "z"), ("records", "a")})
    assert tx.calls[0][1] == ([1, 3, 3], ["z", "a", "b"])
    assert got == {("apps", "z"): 0, ("records", "a"): 1, ("records", "b"): 2}


@pytest.mark.asyncio
async def test_nothing_is_sent_for_nothing() -> None:
    tx = FakeTx()
    w = KgWriter(tx)
    assert await w.records_upsert([]) == 0 and await w.links_set([]) == 0 and await w.nodes_upsert(set()) == {}
    assert tx.calls == []
