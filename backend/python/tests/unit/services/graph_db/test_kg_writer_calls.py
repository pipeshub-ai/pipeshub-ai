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


@pytest.mark.asyncio
async def test_records_go_out_in_sk_order() -> None:
    tx = FakeTx()
    await KgWriter(tx).records_upsert([row(3), row(1), row(2)])
    assert tx.calls[0][1][0] == [1, 2, 3]


@pytest.mark.asyncio
async def test_the_listing_columns_go_out_sorted_by_key_and_in_the_writers_argument_order() -> None:
    tx = FakeTx()
    n = await KgWriter(tx).listing_set([(2, 3, "b", 3, True, False, False, b"b"), (1, 2, "a", 2, False, True, True, None)])
    assert n == 2 and "kg.w_listing_set" in tx.calls[0][0]
    assert tx.calls[0][1] == ([1, 2], [2, 3], ["a", "b"], [2, 3], [False, True], [True, False], [True, False], [None, b"b"])


def test_the_projectors_copy_of_the_records_writers_checks_is_the_manifests() -> None:
    """``record_refusal`` must refuse exactly what ``w_records_upsert`` refuses of a document's values, or a refused row fails its whole batch again."""
    from pathlib import Path

    import yaml

    from app.services.graph_db.postgres.kg.writers import RECORD_ENUMS, RECORD_LENGTHS
    manifest = Path(__file__).resolve().parents[5] / "db" / "versions" / "kg" / "vetted" / "manifest.yaml"
    fn = next(f for f in yaml.safe_load(manifest.read_text(encoding="utf-8"))["functions"] if f["name"] == "kg.w_records_upsert")
    args = list(zip(RECORD_COLS, fn["args"], strict=True))      # the writer takes its arguments in the order of RECORD_COLS
    lengths = {c: tuple(a["item_length"]) for c, a in args if "item_length" in a}
    enums = {c: tuple(a["item_enum"]) for c, a in args if "item_enum" in a}
    assert lengths == RECORD_LENGTHS and enums == RECORD_ENUMS


@pytest.mark.parametrize("over, refused", [({}, None), ({"name": "\U00020000" * 5000, "name_key": "\U00020000" * 5000}, None), ({"rule": "ADMIN"}, "rule"),
                                           ({"external_id": "e" * 2001}, "external_id"), ({"id": ""}, "id"), ({"rule": None, "model": None, "origin": None}, None)])
def test_a_records_row_is_refused_by_the_column_the_writer_would_refuse(over: dict, refused: str | None) -> None:
    from app.services.graph_db.postgres.kg.writers import record_refusal
    base = {**row(1), "rule": "OPEN", "model": "RECORD_LEVEL", "origin": "CONNECTOR"}
    assert record_refusal({**base, **over}) == refused
