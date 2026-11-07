"""A person or entity is shown only to a user who can read one of its records.

These run the tools over the real listing engine and access check, with a
fake graph holding the candidates: user U asks about person P (or entity E)
whose records U cannot open, and must get exactly the reply a name or id that
matches nothing gets, with no email or id of P anywhere in it.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import (
    entity_filters,
    entity_records,
    people,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    execute_find_records_by_entity,
)
from app.agents.actions.knowledge_graph.ops.people import execute_find_records_by_person
from app.modules.retrieval import entity_permissions
from app.modules.retrieval.entity_permissions import (
    PERSON_ENTITY_TYPE,
    EntityAccessContext,
)
from app.services.graph_db.common.utils import EntityCandidateRows
from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

JIRA = "jira-1"
CONTEXT = EntityAccessContext(
    org_id="org-1",
    user_key="ukey",
    app_level_app_ids=frozenset(),
    record_level_app_ids=frozenset({JIRA}),
    record_group_ids=frozenset(),
    app_names={JIRA: "Jira"},
)
PRIYA = {"id": "u-p", "fullName": "Priya Shah", "email": "priya@x.io"}
LEAKS = ("priya@x.io", "u-p")


def _rows(prefix: str, n: int) -> list[dict]:
    return [
        {"_key": f"{prefix}{i}", "recordName": f"Ticket {prefix}{i}", "recordType": "TICKET", "connectorId": JIRA}
        for i in range(n)
    ]


def _state(users: list[dict], candidates: dict[str, list[dict]], *, readable: tuple[str, ...] = (),
           delay: float = 0.0, capped: bool = False) -> dict:
    async def build(refs: list[dict], org: str, *, limit_per_entity: int, offset: int, **_: object) -> dict:
        if delay:
            await asyncio.sleep(delay)
        return {
            (ref["type"], ref["id"]): EntityCandidateRows(
                candidates.get(ref["id"], [])[offset:offset + limit_per_entity], capped=capped,
            )
            for ref in refs
        }

    graph = entity_graph(build, permitted=readable)
    graph.find_users = AsyncMock(side_effect=lambda org, query, **_: [
        u for u in users
        if query.casefold() in (u["email"].casefold(), u["fullName"].casefold())
    ])
    graph.find_people = AsyncMock(return_value=[])
    return {"org_id": "org-1", "user_id": "user-1", "user_email": "me@x.io", "graph_provider": graph}


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=CONTEXT))
    monkeypatch.setattr(people, "resolve_frontend_url", AsyncMock(return_value=None))
    monkeypatch.setattr(people, "cite_listed_records", AsyncMock(return_value={}))
    monkeypatch.setattr(entity_records, "resolve_frontend_url", AsyncMock(return_value=None))
    monkeypatch.setattr(entity_records, "cite_listed_records", AsyncMock(return_value={}))


async def _nobody(person: str, **kwargs: object) -> tuple[bool, str]:
    return await execute_find_records_by_person(_state([], {}), person, **kwargs)


def _no_leak(text: str, asked: str = "") -> None:
    """Nothing of P's beyond what the caller typed."""
    assert not any(leak in text for leak in LEAKS if leak != asked), text


class TestPersonWithNoReadableRecord:
    @pytest.mark.parametrize("asked", ["Priya Shah", "priya@x.io"])
    async def test_more_unreadable_records_than_one_scan_reads_as_no_one(self, asked: str) -> None:
        state = _state([PRIYA], {"u-p": _rows("p", 1500)})
        reply = await execute_find_records_by_person(state, asked)
        assert reply == await _nobody(asked)
        _no_leak(reply[1], asked)

    async def test_a_few_unreadable_records_read_as_no_one(self) -> None:
        reply = await execute_find_records_by_person(_state([PRIYA], {"u-p": _rows("p", 3)}), "Priya Shah")
        assert reply == await _nobody("Priya Shah")

    async def test_a_forged_cursor_reads_as_no_one(self) -> None:
        state = _state([PRIYA], {"u-p": _rows("p", 3)})
        reply = await execute_find_records_by_person(state, "priya@x.io", cursor="40")
        assert reply == await _nobody("priya@x.io", cursor="40")

    async def test_a_malformed_cursor_reads_the_same_for_anyone(self) -> None:
        state = _state([PRIYA], {"u-p": _rows("p", 3)})
        reply = await execute_find_records_by_person(state, "priya@x.io", cursor="not-a-cursor")
        assert reply == await _nobody("priya@x.io", cursor="not-a-cursor")

    async def test_a_probe_that_runs_out_of_time_reads_as_no_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(entity_permissions, "LISTING_DEADLINE_SECONDS", 0.05)
        state = _state([PRIYA], {"u-p": _rows("p", 3)}, delay=0.8)
        reply = await execute_find_records_by_person(state, "Priya Shah")
        assert reply == await _nobody("Priya Shah")

    async def test_many_namesakes_with_no_readable_record_read_as_no_one(self) -> None:
        many = [{"id": f"u-{i}", "fullName": "Alex Kim", "email": f"alex{i}@x.io"} for i in range(7)]
        state = _state(many, {f"u-{i}": _rows(f"a{i}-", 2) for i in range(7)})
        reply = await execute_find_records_by_person(state, "Alex Kim")
        assert reply == await _nobody("Alex Kim")
        # Every namesake is decided in one round, not one listing each.
        assert state["graph_provider"].get_permitted_entity_records.await_count == 1


class TestNamesakes:
    async def test_a_readable_namesake_is_listed_and_the_other_never_hinted_at(self) -> None:
        sam = {"id": "u-s", "fullName": "Priya Shah", "email": "pshah@y.io"}
        state = _state([PRIYA, sam], {"u-p": _rows("p", 3), "u-s": _rows("s", 2)}, readable=("s1",))
        ok, text = await execute_find_records_by_person(state, "Priya Shah")
        assert ok and text.startswith("Records linked to Priya Shah (pshah@y.io)")
        assert "Ticket s1" in text
        _no_leak(text)
        assert "Other people" not in text and "also match" not in text

    async def test_readable_namesakes_past_the_old_probe_limit_are_offered_and_only_they(self) -> None:
        many = [{"id": f"u-{i}", "fullName": "Alex Kim", "email": f"alex{i}@x.io"} for i in range(7)]
        state = _state(many, {f"u-{i}": _rows(f"a{i}-", 2) for i in range(7)}, readable=("a5-1", "a6-0"))
        ok, text = await execute_find_records_by_person(state, "Alex Kim")
        assert ok and text.splitlines()[0] == 'Several people match "Alex Kim":'
        assert "alex5@x.io" in text and "alex6@x.io" in text
        assert not any(f"alex{i}@x.io" in text for i in range(5))
        assert "Others match" not in text and "Many people" not in text


class TestReadableRecordFarDown:
    async def test_a_person_whose_only_readable_record_is_past_one_scan_is_listed(self) -> None:
        rows = _rows("p", 1600)
        state = _state([PRIYA], {"u-p": rows}, readable=("p1500",))
        ok, text = await execute_find_records_by_person(state, "Priya Shah")
        assert ok and "Ticket p1500" in text
        assert "That is all 1 readable, indexed record(s)." in text


class TestEntityListing:
    async def _unknown(self) -> tuple[bool, str]:
        return await execute_find_records_by_entity(_state([], {}), "t-none", entity_type="topic")

    async def test_more_unreadable_records_than_one_scan_read_as_an_unknown_entity(self) -> None:
        state = _state([], {"t1": _rows("t", 1500)})
        _, text = await execute_find_records_by_entity(state, "t1", entity_type="topic")
        assert text == (await self._unknown())[1]
        assert "cursor" not in text

    async def test_a_capped_entity_with_nothing_readable_reads_as_an_unknown_one(self) -> None:
        state = _state([], {"t1": _rows("t", 5)}, capped=True)
        _, text = await execute_find_records_by_entity(state, "t1", entity_type="topic")
        assert text == (await self._unknown())[1]

    async def test_a_readable_record_past_one_scan_is_listed(self) -> None:
        state = _state([], {"t1": _rows("t", 1600)}, readable=("t1500",))
        ok, text = await execute_find_records_by_entity(state, "t1", entity_type="topic")
        assert ok and "Ticket t1500" in text


class TestProbe:
    async def test_offsets_are_each_refs_first_readable_candidate(self) -> None:
        state = _state([], {"u-a": _rows("a", 900), "u-b": _rows("b", 30), "u-c": []}, readable=("a850", "b3"))
        refs = [entity_permissions._person_ref([("users", uid)], ["ASSIGNED_TO"]) for uid in ("u-a", "u-b", "u-c")]
        found = await entity_permissions.first_readable_offsets(state["graph_provider"], CONTEXT, refs)
        assert found == {(PERSON_ENTITY_TYPE, "u-a"): 850, (PERSON_ENTITY_TYPE, "u-b"): 3}
