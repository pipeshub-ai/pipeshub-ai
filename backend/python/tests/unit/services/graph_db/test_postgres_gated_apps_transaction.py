"""``get_gated_apps`` hands its transaction to ``get_user_apps`` by keyword, so a provider that extends ``get_user_apps`` with an argument
before ``transaction`` still gets the transaction where it belongs (and not as that other argument)."""
from __future__ import annotations

from app.core.org_context import for_org
from app.services.graph_db.postgres.provider import PostgresGraphProvider

ORG = "a" * 24


class _Extended(PostgresGraphProvider):
    seen: list[tuple] = []

    async def get_user_apps(self, user_id: str, org_id: str | None = None, transaction: str | None = None) -> list:  # type: ignore[override]
        self.seen.append((user_id, org_id, transaction))
        return [{"_key": "app-1"}]


async def test_the_transaction_reaches_get_user_apps_by_keyword() -> None:
    p = object.__new__(_Extended)        # no database: the call under test reads none
    with for_org(ORG, job="t"):
        assert await p.get_gated_apps("u1", ORG, transaction="tx-1") == [{"_key": "app-1"}]
        assert await p.get_gated_apps("u1", "b" * 24, transaction="tx-1") == []
    assert _Extended.seen == [("u1", None, "tx-1")]
