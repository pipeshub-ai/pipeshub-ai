"""``kg.locks``: the org-wide key first (exclusive for a projection that spans connectors, shared otherwise), then the connectors in the order of their lock keys."""
from __future__ import annotations

import pytest

from app.services.graph_db.postgres.kg.locks import LOCK_CLASS, lock_apps


class FakeTx:
    def __init__(self, keys: dict[str, int]) -> None:
        self.keys, self.calls = keys, []

    async def execute(self, sql: str, *args: object) -> None:
        self.calls.append((sql, args))

    async def fetch(self, sql: str, org: str, apps: list[str]) -> list:
        return [{"app": a, "k": self.keys[a]} for a in apps]


@pytest.mark.asyncio
async def test_the_org_key_comes_first_and_the_connectors_follow_in_lock_key_order() -> None:
    tx = FakeTx({"a": 30, "b": 10, "c": 10})                       # b and c share a key: the id breaks the tie
    await lock_apps(tx, "o", {"a", "b", "c", ""}, org_wide=False)
    first_sql, first_args = tx.calls[0]
    assert ("shared" in first_sql, first_args) == (True, (LOCK_CLASS, "o|"))
    assert [args[1] for _, args in tx.calls[1:]] == [10, 10, 30]
    assert len(tx.calls) == 4


@pytest.mark.asyncio
async def test_an_org_wide_projection_takes_the_org_key_exclusively_and_nothing_else_without_connectors() -> None:
    tx = FakeTx({})
    await lock_apps(tx, "o", {""}, org_wide=True)
    assert len(tx.calls) == 1 and "shared" not in tx.calls[0][0] and "lock_org_exclusive" in tx.calls[0][0]
