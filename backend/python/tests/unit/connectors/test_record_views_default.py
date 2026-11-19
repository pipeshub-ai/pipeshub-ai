"""The default record view shows a record as stored, and a decorator can be installed per process."""
from __future__ import annotations

from app.connectors import record_views
from app.connectors.record_views import DefaultRecordViewDecorator, set_decorator, views


async def test_the_default_decorator_changes_nothing() -> None:
    decorator = views()
    rows = [{"_key": "a"}]
    record = {"_key": "a"}
    stats = {"total": 1}

    assert isinstance(decorator, DefaultRecordViewDecorator)
    assert await decorator.decorate_rows(rows, object(), surface="kb_records") is rows
    assert await decorator.decorate_record(record, object(), surface="record") is record
    assert await decorator.decorate_stats(stats, object(), surface="connector_stats") is stats
    assert await decorator.stream_source(record, object()) is None
    assert await decorator.reindex_connector_extra(
        object(), object(), org_id="o", connector_id="c", statuses=["FAILED"], user_key="u"
    ) == 0


async def test_an_installed_decorator_is_the_one_views_returns_and_reset_restores_the_default() -> None:
    class Mine(DefaultRecordViewDecorator):
        async def decorate_rows(self, rows: list, graph: object, *, surface: str) -> list:
            return [{**r, "surface": surface} for r in rows]

    set_decorator(Mine())

    assert await views().decorate_rows([{"_key": "a"}], object(), surface="s") == [{"_key": "a", "surface": "s"}]

    record_views.reset_decorator()

    assert isinstance(views(), DefaultRecordViewDecorator)


async def test_a_decorator_installed_by_one_test_is_gone_in_the_next_one_part_1() -> None:
    class Mine(DefaultRecordViewDecorator):
        pass

    set_decorator(Mine())


async def test_a_decorator_installed_by_one_test_is_gone_in_the_next_one_part_2() -> None:
    assert type(views()) is DefaultRecordViewDecorator
