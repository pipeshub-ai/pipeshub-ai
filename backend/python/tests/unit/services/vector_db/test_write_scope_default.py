"""The default write scope owns every point of the record's virtual record id, and a resolver can be installed per process."""
from __future__ import annotations

from types import SimpleNamespace

from app.services.vector_db import write_scope
from app.services.vector_db.write_scope import (
    WholeRecordScope,
    scope_for,
    set_scope_resolver,
)


async def test_the_default_scope_owns_the_whole_virtual_record() -> None:
    scope = await scope_for(SimpleNamespace(virtual_record_id="v1"), {"settings": {}})

    assert isinstance(scope, WholeRecordScope)
    assert (scope.content_vrid, scope.read_vrid) == ("v1", "v1")
    assert scope.must() == {"virtualRecordId": "v1"}
    assert scope.point_salt == ""
    assert scope.is_scoped is False
    assert scope.writes_summary is True
    assert scope.reconciles is True
    assert scope.payload({"a": 1, "virtualRecordId": "other"}) == {"a": 1, "virtualRecordId": "v1"}


async def test_a_record_given_as_a_document_is_read_by_its_virtual_record_id() -> None:
    scope = await scope_for({"virtualRecordId": "v2"})

    assert scope.must() == {"virtualRecordId": "v2"}


async def test_an_installed_resolver_decides_and_is_asked_with_the_record_and_the_run() -> None:
    seen: list[tuple[object, object]] = []

    class Mine(WholeRecordScope):
        pass

    async def resolver(record: object, ctx: object, write: bool) -> Mine:
        seen.append((record, ctx))
        return Mine("c", "r", point_salt="s", is_scoped=True)

    set_scope_resolver(resolver)
    record = SimpleNamespace(virtual_record_id="v1")

    scope = await scope_for(record, {"k": 1})

    assert seen == [(record, {"k": 1})]
    assert scope.point_salt == "s" and scope.is_scoped is True


async def test_the_reset_restores_the_default() -> None:
    async def resolver(record: object, ctx: object, write: bool) -> WholeRecordScope:
        return WholeRecordScope("c", "r", point_salt="s")

    set_scope_resolver(resolver)
    write_scope.reset_scope_resolver()

    assert (await scope_for(SimpleNamespace(virtual_record_id="v1"))).point_salt == ""


async def test_a_resolver_installed_by_one_test_is_gone_in_the_next_one_part_1() -> None:
    async def resolver(record: object, ctx: object, write: bool) -> WholeRecordScope:
        return WholeRecordScope("c", "r", point_salt="leak")

    set_scope_resolver(resolver)


async def test_a_resolver_installed_by_one_test_is_gone_in_the_next_one_part_2() -> None:
    assert (await scope_for(SimpleNamespace(virtual_record_id="v1"))).point_salt == ""


async def test_the_resolver_is_told_whether_the_scope_is_for_a_write_and_a_read_is_the_default() -> None:
    seen: list[bool] = []

    async def resolver(record: object, ctx: object, write: bool) -> WholeRecordScope:
        seen.append(write)
        return WholeRecordScope.of("v1")

    set_scope_resolver(resolver)
    record = SimpleNamespace(virtual_record_id="v1")
    await scope_for(record)
    await scope_for(record, write=True)
    assert seen == [False, True]


async def test_the_vector_store_asks_for_a_write_scope_everywhere_it_writes_or_deletes() -> None:
    """Every ``_scope_for`` and ``scope_for`` call in the vector store names ``write=``; none is left to the default."""
    import ast
    from pathlib import Path

    source = (Path(__file__).resolve().parents[4] / "app/modules/transformers/vectorstore.py").read_text()
    calls = [
        n for n in ast.walk(ast.parse(source))
        if isinstance(n, ast.Call) and getattr(n.func, "attr", getattr(n.func, "id", None)) in {"_scope_for", "scope_for"}
    ]
    assert len(calls) >= 4
    assert all("write" in {k.arg for k in c.keywords} for c in calls), [c.lineno for c in calls if "write" not in {k.arg for k in c.keywords}]
