"""A person or entity is shown only to a user who can read one of its records.

These run the tools over the real listing engine and access check, with a
fake graph holding the candidates: user U asks about person P (or entity E)
whose records U cannot open, and must get exactly the reply a name or id that
matches nothing gets, with no email or id of P anywhere in it.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import (
    entity_filters,
    entity_records,
    merged_listings,
    people,
    record_query,
)
from app.agents.actions.knowledge_graph.ops.entity_records import (
    execute_find_records_by_entity,
)
from app.agents.actions.knowledge_graph.ops.people import execute_find_records_by_person
from app.agents.agent_loop.knowledge_graph_policy import (
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
)
from app.modules.retrieval import entity_permissions
from app.modules.retrieval.entity_permissions import (
    PERSON_ENTITY_TYPE,
    EntityAccessContext,
)
from app.services.graph_db.common.record_query_rows import (
    CandidateKey,
    RecordCandidates,
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
        refs = [entity_permissions._person_ref([uid], ["ASSIGNED_TO"]) for uid in ("u-a", "u-b", "u-c")]
        found = await entity_permissions.first_readable_offsets(state["graph_provider"], CONTEXT, refs)
        assert found == {(PERSON_ENTITY_TYPE, "u-a"): 850, (PERSON_ENTITY_TYPE, "u-b"): 3}


# ------------------------------------------------- find_records and merged names

OUTSIDER = {"id": "pn-1", "name": "Ola Outsider", "email": "ola@ext.io"}


def _query_state(users: list[dict], candidates: dict[str, list[dict]], *, readable: tuple[str, ...] = (),
                 scanned: dict[str, int] | None = None, nodes: list[dict] = ()) -> dict:
    """``_state`` with record query on. ``scanned[id]`` is how many unreadable
    candidates a find_records scan meets for a condition naming ``id``, as a
    scan the provider stopped at its cap."""
    state = _state(users, candidates, readable=readable)
    graph = state["graph_provider"]
    unreadable = {f"q-{key}-{i}" for key, n in (scanned or {}).items() for i in range(n)}
    graph.fake._denied |= unreadable

    async def scan(org: str, *, pushdown: dict, scan_cap: int, **_: object) -> RecordCandidates:
        named = json.dumps(pushdown, default=str)
        keys = [CandidateKey(f"q-{key}-{i}", 1_000 - i) for key, n in (scanned or {}).items()
                if f'"{key}"' in named for i in range(n)]
        return RecordCandidates(keys[:scan_cap], capped=bool(keys))

    graph.get_record_query_candidates = AsyncMock(side_effect=scan)
    graph.get_record_query_rows = AsyncMock(return_value=[])
    graph.get_person_nodes = AsyncMock(side_effect=lambda org, ids, **_: [n for n in nodes if n["id"] in ids])
    state[KNOWLEDGE_GRAPH_POLICY_STATE_KEY] = KnowledgeGraphPolicy(
        mode=KnowledgeGraphMode.FULL, named_entities=True, record_query=True,
    )
    return state


@pytest.fixture
def query_wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(record_query, "cite_listed_records", AsyncMock(return_value={}))
    monkeypatch.setattr(record_query, "start_search", lambda *a, **k: None)
    monkeypatch.setattr(record_query, "finish_recall", AsyncMock(return_value=None))


def _same_but_id(text: str, other: str, ours: str, theirs: str) -> None:
    assert text.replace(ours, "<id>") == other.replace(theirs, "<id>")


@pytest.mark.usefixtures("query_wiring")
class TestFindRecordsPersonByName:
    """V3: the person-by-name path of find_records resolves names as
    find_records_by_person does, so a name whose people the user shares no
    readable record with reads as a name no one has, and nothing is run."""

    async def _nobody(self, where: list[dict]) -> tuple[bool, str]:
        return await record_query.execute_find_records(_query_state([], {}), where=where)

    @pytest.mark.parametrize("asked", ["Priya Shah", "priya@x.io"])
    async def test_a_person_with_only_unreadable_records_reads_as_no_one(self, asked: str) -> None:
        where = [{"person": {"name": asked}}]
        state = _query_state([PRIYA], {"u-p": _rows("p", 1500)})
        reply = await record_query.execute_find_records(state, where=where)
        assert reply == await self._nobody(where)
        assert "Total:" not in reply[1] and "Conditions as applied" not in reply[1]
        _no_leak(reply[1], asked)
        state["graph_provider"].get_record_query_candidates.assert_not_awaited()

    async def test_many_unreadable_namesakes_read_as_no_one(self) -> None:
        many = [{"id": f"u-{i}", "fullName": "Alex Kim", "email": f"alex{i}@x.io"} for i in range(7)]
        where = [{"person": {"name": "Alex Kim"}}]
        state = _query_state(many, {f"u-{i}": _rows(f"a{i}-", 2) for i in range(7)})
        reply = await record_query.execute_find_records(state, where=where)
        assert reply == await self._nobody(where)
        assert "Many people" not in reply[1] and "alex" not in reply[1]

    async def test_only_the_readable_namesake_is_named_and_run(self) -> None:
        sam = {"id": "u-s", "fullName": "Priya Shah", "email": "pshah@y.io"}
        state = _query_state([PRIYA, sam], {"u-p": _rows("p", 3), "u-s": _rows("s", 2)}, readable=("s1",))
        ok, text = await record_query.execute_find_records(state, where=[{"person": {"name": "Priya Shah"}}])
        assert ok and 'Person "Priya Shah" is Priya Shah (pshah@y.io).' in text
        _no_leak(text)
        assert "Others match" not in text and "Several people" not in text

    async def test_readable_namesakes_past_the_old_probe_limit_are_offered_and_only_they(self) -> None:
        many = [{"id": f"u-{i}", "fullName": "Alex Kim", "email": f"alex{i}@x.io"} for i in range(7)]
        state = _query_state(many, {f"u-{i}": _rows(f"a{i}-", 2) for i in range(7)}, readable=("a5-1", "a6-0"))
        ok, text = await record_query.execute_find_records(state, where=[{"person": {"name": "Alex Kim"}}])
        assert ok and text.splitlines()[0] == 'Several people match "Alex Kim":'
        assert "alex5@x.io" in text and "alex6@x.io" in text
        assert not any(f"alex{i}@x.io" in text for i in range(5)) and "Others match" not in text

    async def test_the_merged_person_tool_reads_the_same_as_for_no_one(self) -> None:
        state = _query_state([PRIYA], {"u-p": _rows("p", 1500)})
        reply = await merged_listings.find_records_for_person(state, person="Priya Shah", roles=["assignee"])
        nobody = await merged_listings.find_records_for_person(
            _query_state([], {}), person="Priya Shah", roles=["assignee"],
        )
        assert reply == nobody
        _no_leak(reply[1])


@pytest.mark.usefixtures("query_wiring")
class TestFindRecordsById:
    """An organisation, outsider or person given by id whose records the user
    cannot read reads as an id that names nothing, also when the scan stopped
    among its unreadable records."""

    @pytest.mark.parametrize(("kind", "ours", "nodes"), [
        ("organization", "o-1", []),
        ("person", "pn-1", [OUTSIDER]),
        ("person", "u-p", [{"id": "u-p", "name": "Priya Shah", "email": "priya@x.io"}]),
    ])
    async def test_a_capped_scan_of_unreadable_records_reads_as_an_unknown_id(
        self, kind: str, ours: str, nodes: list[dict],
    ) -> None:
        state = _query_state([], {}, scanned={ours: 1500}, nodes=nodes)
        ok, text = await merged_listings.find_records_for_entity(state, entity_id=ours, entity_type=kind)
        _, unknown = await merged_listings.find_records_for_entity(
            _query_state([], {}, nodes=nodes), entity_id="none-1", entity_type=kind,
        )
        assert ok
        _same_but_id(text, unknown, ours, "none-1")
        assert "approximate" not in text and "ola@ext.io" not in text and "priya@x.io" not in text

    async def test_find_records_itself_reads_an_unreadable_organisation_as_an_unknown_one(self) -> None:
        state = _query_state([], {}, scanned={"o-1": 1500})
        _, text = await record_query.execute_find_records(state, where=[{"organization": {"ids": ["o-1"]}}])
        _, unknown = await record_query.execute_find_records(
            _query_state([], {}), where=[{"organization": {"ids": ["none-1"]}}],
        )
        _same_but_id(text, unknown, "o-1", "none-1")

