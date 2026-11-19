"""The gauges the worker publishes (no database): one org's pass leaves the series of the others, and a series whose records are gone reads 0."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg import legacy

if TYPE_CHECKING:
    import pytest


class Gauge:
    def __init__(self) -> None:
        self.values: dict[tuple, float] = {}
        self.cleared = 0

    def set(self, *labels: str, value: float) -> None:
        self.values[labels] = value

    def clear(self) -> None:
        self.cleared += 1
        self.values.clear()


class Tx:
    def __init__(self, legacy_rows: list[dict]) -> None:
        self.legacy_rows = legacy_rows

    async def fetch(self, sql: str, *args: object) -> list[dict]:
        if "kg.app_names" in sql:
            return [{"sk": 1, "id": "app1"}]
        if "kg.legacy_counts" in sql:
            return self.legacy_rows
        return []                                       # kg.poison

    async def fetchval(self, sql: str, *args: object) -> int:
        return 7


async def test_publishing_one_org_leaves_the_series_of_the_others_and_zeroes_a_series_whose_records_are_gone(monkeypatch: pytest.MonkeyPatch) -> None:
    gauge = Gauge()
    monkeypatch.setattr(legacy, "LEGACY", gauge)
    monkeypatch.setattr(legacy, "_published", {})
    row = [{"app_sk": 1, "legacy_reason": 3, "n": 5}]
    await legacy.publish(Tx(row), "org-a")                        # type: ignore[arg-type]
    await legacy.publish(Tx(row), "org-b")                        # type: ignore[arg-type]
    assert gauge.cleared == 0 and gauge.values == {("org-a", "app1", "3"): 5, ("org-b", "app1", "3"): 5}
    await legacy.publish(Tx([]), "org-a")                         # type: ignore[arg-type]
    assert gauge.values == {("org-a", "app1", "3"): 0, ("org-b", "app1", "3"): 5}
