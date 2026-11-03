"""``find_records`` as the one listing tool (``docs/record-query.md``):
people by name, email or ``me`` resolved as ``find_records_by_person``
resolves them, the ``mentions`` gate, and the three merged tools' old names
routed to the equivalent query."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import merged_listings
from app.agents.actions.knowledge_graph.ops import people as people_op
from app.agents.actions.knowledge_graph.ops import record_query as op
from app.agents.actions.knowledge_graph.ops.entity_discovery import (
    NAMED_ENTITIES_DISABLED_MSG,
)
from app.agents.actions.knowledge_graph.ops.entity_filters import ENTITY_INDEX_CACHE_KEY
from app.agents.agent_loop.knowledge_graph_policy import (
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
)
from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.modules.retrieval.record_query.model import (
    AnyOf,
    FieldCondition,
    MentionCondition,
    PersonCondition,
    RecordQueryResult,
)

CONTEXT = EntityAccessContext(
    org_id="org", user_key="ukey", app_level_app_ids=frozenset(), record_level_app_ids=frozenset({"jira"}),
    record_group_ids=frozenset(), app_names={"jira": "Jira"},
)
PRIYA = {"id": "u-p", "fullName": "Priya Nair", "email": "priya@x.io"}
PRIYA_GUEST = {"id": "p-p", "fullName": "Priya Nair", "email": "PRIYA@x.io"}
NAMESAKE = {"id": "u-n", "fullName": "Priya Nair", "email": "pnair@y.io"}


def _state(users: list[dict] = (), *, named: bool = True, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    graph = MagicMock()
    graph.find_users = AsyncMock(return_value=list(users))
    policy = KnowledgeGraphPolicy(mode=KnowledgeGraphMode.FULL, named_entities=named, record_query=True)
    return {"graph_provider": graph, "org_id": "org", "user_email": "me@x.io",
            KNOWLEDGE_GRAPH_POLICY_STATE_KEY: policy, **extra}


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    run = AsyncMock(return_value=RecordQueryResult(total=3))
    monkeypatch.setattr(op, "run_record_query", run)
    monkeypatch.setattr(op, "load_entity_access_context", AsyncMock(return_value=CONTEXT))
    monkeypatch.setattr(op, "cite_listed_records", AsyncMock(return_value={}))
    monkeypatch.setattr(op, "remember_record_ids", MagicMock())
    monkeypatch.setattr(op, "note_retrieved_by", MagicMock())
    return run


@pytest.fixture
def readable(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """User id -> the readable record keys the visibility probe finds for that person."""
    pages: dict[str, list[str]] = {}

    async def offsets(graph: object, context: object, people: list[list[str]], **_: Any) -> list[int | None]:  # noqa: ANN401
        return [0 if pages.get(ids[0]) else None for ids in people]

    monkeypatch.setattr(people_op, "readable_person_offsets", AsyncMock(side_effect=offsets))
    return pages


def _ran(engine: AsyncMock) -> Any:  # noqa: ANN401
    return engine.await_args.args[2]


async def test_a_name_naming_one_person_runs_with_every_node_of_their_email(
    engine: AsyncMock, readable: dict[str, list[str]],
) -> None:
    readable["u-p"] = ["r1"]
    ok, text = await op.execute_find_records(
        _state([PRIYA, PRIYA_GUEST]), where=[{"person": {"role": "assignee", "name": "priya nair"}}],
    )
    assert ok
    (cond,) = _ran(engine).where
    assert isinstance(cond, PersonCondition) and cond.person.ids == ["u-p", "p-p"]
    assert 'Person "priya nair" is Priya Nair (priya@x.io).' in text
    people_op.readable_person_offsets.assert_awaited()
    assert people_op.readable_person_offsets.await_args.kwargs["edge_types"] == ["ASSIGNED_TO"]


async def test_a_name_naming_several_people_lists_them_and_never_guesses(
    engine: AsyncMock, readable: dict[str, list[str]],
) -> None:
    readable["u-p"], readable["u-n"] = ["r1"], ["r2"]
    ok, text = await op.execute_find_records(_state([PRIYA, NAMESAKE]), where=[{"person": {"name": "Priya Nair"}}])
    assert ok and "Several people match" in text
    assert "priya@x.io" in text and "pnair@y.io" in text and "find_records again" in text
    engine.assert_not_awaited()


async def test_a_namesake_with_nothing_readable_is_never_named(
    engine: AsyncMock, readable: dict[str, list[str]],
) -> None:
    readable["u-p"] = ["r1"]
    _, text = await op.execute_find_records(_state([PRIYA, NAMESAKE]), where=[{"person": {"name": "Priya Nair"}}])
    assert "pnair@y.io" not in text
    assert _ran(engine).where[0].person.ids == ["u-p"]


async def test_a_name_naming_no_one_says_so_and_points_at_search(
    engine: AsyncMock, readable: dict[str, list[str]],
) -> None:
    ok, text = await op.execute_find_records(_state([]), where=[{"person": {"name": "Nobody Here"}}])
    assert ok and "No readable records are linked to a person matching" in text
    engine.assert_not_awaited()


async def test_me_is_the_user_asking(engine: AsyncMock, readable: dict[str, list[str]]) -> None:
    await op.execute_find_records(_state(), where=[{"person": {"name": "me"}}])
    assert _ran(engine).where[0].person.ids == ["me"]


async def test_mentions_need_named_entities(engine: AsyncMock) -> None:
    ok, text = await op.execute_find_records(_state(named=False), where=[{"mentions": {"name": "Acme"}}])
    assert (ok, text) == (False, NAMED_ENTITIES_DISABLED_MSG)
    engine.assert_not_awaited()


async def test_a_mention_is_reported_as_a_text_mention_never_a_field(engine: AsyncMock) -> None:
    ok, text = await op.execute_find_records(_state(), where=[{"mentions": {"name": "Acme"}}])
    assert ok and op.MENTIONS_NOTE in text and "never total or group" in text


# ------------------------------------------------------------- merged names


async def test_find_records_by_person_runs_as_find_records_and_says_it_was_merged(
    engine: AsyncMock, readable: dict[str, list[str]],
) -> None:
    readable["u-p"] = ["r1"]
    ok, text = await merged_listings.find_records_for_person(
        _state([PRIYA]), person="Priya Nair", roles=["assignee", "involved"], record_types=["ticket"], limit=5,
    )
    assert ok and text.startswith("knowledgegraph__find_records_by_person was merged into knowledgegraph__find_records")
    query = _ran(engine)
    any_of, types = query.where
    assert isinstance(any_of, AnyOf) and [c.person.role for c in any_of.any_of] == ["assignee", "participant"]
    assert all(c.person.ids == ["u-p"] for c in any_of.any_of)
    assert isinstance(types, FieldCondition) and types.in_ == ["TICKET"] and query.limit == 5


async def test_find_records_by_entity_routes_each_entity_type_to_its_condition(engine: AsyncMock) -> None:
    state = _state(**{ENTITY_INDEX_CACHE_KEY: {"t1": {"type": "topic", "name": "Billing"}}})
    await merged_listings.find_records_for_entity(state, entity_id="t1")
    assert _ran(engine).where[0].entity.model_dump() == {"type": "topic", "id": "t1"}
    await merged_listings.find_records_for_entity(state, entity_id="o1", entity_type="organization")
    assert _ran(engine).where[0].organization.ids == ["o1"]
    await merged_listings.find_records_for_entity(state, entity_id="u1", entity_type="person")
    assert _ran(engine).where[0].person.ids == ["u1"]


async def test_an_old_cursor_cannot_continue_and_says_what_to_call(engine: AsyncMock) -> None:
    ok, text = await merged_listings.find_records_for_entity(
        _state(), entity_id="g1", entity_type="record_group", cursor="20",
    )
    assert ok is False and 'knowledgegraph__find_records(where=[{"entity"' in text
    engine.assert_not_awaited()


async def test_find_records_by_value_keeps_date_to_inclusive_and_its_sources(engine: AsyncMock) -> None:
    mar_1, mar_31, apr_1 = 1_772_323_200_000, 1_774_915_200_000, 1_775_001_600_000
    ok, _ = await merged_listings.find_records_for_value(
        _state(), kinds=["date"], date_from=mar_1, date_to=mar_31, amount_min=0, amount_max=0,
        source_ids=["jira"],
    )
    assert ok
    mention, = _ran(engine).where
    assert isinstance(mention, MentionCondition) and mention.mentions.amount is None
    assert mention.entity_filter()["mentionedDate"] == {"from": mar_1, "to": apr_1}
    assert engine.await_args.kwargs["apps"] == ["jira"]


async def test_find_records_by_value_with_a_knowledge_base_reads_it_as_a_container(engine: AsyncMock) -> None:
    await merged_listings.find_records_for_value(_state(), name="Acme", source_ids=["kb-1"])
    mention, container = _ran(engine).where
    assert container.field == "container" and container.in_ == ["kb-1"]
    assert engine.await_args.kwargs["apps"] is None

