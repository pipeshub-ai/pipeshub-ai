"""The typed graph's read is on unless PG_KG_READ is 0."""
import pytest

from app.services.graph_db.postgres.kg import read as kg_read


@pytest.mark.parametrize(("value", "want"), [(None, True), ("", True), ("0", False), ("1", True)])
def test_the_read_is_on_unless_the_switch_is_zero(monkeypatch, value, want) -> None:
    monkeypatch.delenv("PG_KG_READ", raising=False)
    if value is not None:
        monkeypatch.setenv("PG_KG_READ", value)
    assert kg_read.enabled() is want


async def test_a_grant_probe_is_left_to_the_walk_without_reading_kg() -> None:
    async def run(fn) -> None:
        raise AssertionError("kg was read")

    async def walk(ids, vrids, acc) -> list:
        return [{"id": "w"}]
    assert await kg_read.accessible_rows(run, "o", ["a"], [], {"probe": True}, walk) == [{"id": "w"}]


async def test_a_failing_read_is_answered_by_the_walk() -> None:
    async def run(fn) -> None:
        raise RuntimeError("down")

    async def walk(ids, vrids, acc) -> list:
        return [{"id": "w"}]
    assert await kg_read.accessible_rows(run, "o", ["a"], [], {"gated_app_ids": [], "by_connector": {}}, walk) == [{"id": "w"}]


async def test_virtual_record_ids_are_always_the_walks() -> None:
    seen = []

    async def run(fn) -> tuple:
        return kg_read.Decision(admit={"a"}), [{"id": "a"}]

    async def walk(ids, vrids, acc) -> list:
        seen.append((ids, vrids))
        return [{"id": "v"}]
    got = await kg_read.accessible_rows(run, "o", ["a"], ["vr"], {"gated_app_ids": [], "by_connector": {}}, walk)
    assert seen == [([], ["vr"])] and {r["id"] for r in got} == {"a", "v"}


@pytest.mark.parametrize("bad", ["true", "false", "yes", "2", "off"])
def test_any_other_value_is_an_error_that_names_the_setting(monkeypatch, bad) -> None:
    monkeypatch.setenv("PG_KG_READ", bad)
    with pytest.raises(ValueError, match="PG_KG_READ"):
        kg_read.enabled()
