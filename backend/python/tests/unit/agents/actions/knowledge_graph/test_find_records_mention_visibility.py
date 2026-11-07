"""find_records' ``mentions`` condition, and find_records_by_value's old name
routed to it, confirm a value only to a user who can read a record mentioning it.

These run the tool over the real record query engine, with a fake graph whose
access check admits only ``readable``. User U asks about a value V that only
records U cannot open mention, and must get exactly the reply a value nobody
mentions gets: no "approximate", no "at least", no cursor, no record id.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import merged_listings
from app.agents.actions.knowledge_graph.ops import record_query as op
from app.agents.agent_loop.knowledge_graph_policy import (
    KNOWLEDGE_GRAPH_POLICY_STATE_KEY,
    KnowledgeGraphMode,
    KnowledgeGraphPolicy,
)
from app.modules.retrieval.entity_permissions import EntityAccessContext
from app.modules.retrieval.record_query import engine, recall
from app.modules.retrieval.record_query.engine import SCAN_CAP, TOO_BROAD_MENTION_MSG
from app.services.graph_db.common.record_query_rows import (
    CandidateKey,
    RecordCandidates,
)

pytestmark = pytest.mark.asyncio

APP = "jira"
CONTEXT = EntityAccessContext(
    org_id="org", user_key="ukey", app_level_app_ids=frozenset(), record_level_app_ids=frozenset({APP}),
    record_group_ids=frozenset(), app_names={APP: "Jira"},
)
# More hits than one lookup lists: the value is too broad across the org.
_BROAD = SCAN_CAP + 5000


def _graph(mentioned: int, readable: set[str] = frozenset(), *, searched: list[str] = ()) -> MagicMock:
    """V is mentioned in ``mentioned`` records ``m00000``..., newest first; the
    access check admits ``readable``; search finds ``searched``."""
    hits = [{"recordId": f"m{i:05}", "connectorId": APP, "virtualRecordId": f"v-m{i:05}", "sortTs": 100_000 - i}
            for i in range(mentioned)]
    graph = MagicMock()

    async def named(org_id: str, entity_ids: Any, query: Any, *, limit: int, within: list[str] | None = None,  # noqa: ANN401
                    **_: Any) -> dict:  # noqa: ANN401
        allowed = None if within is None else set(within)
        found = [h for h in hits if allowed is None or h["recordId"] in allowed]
        return {"hits": found[:limit], "truncated": len(found) > limit}

    async def accessible(user_id: str, org_id: str, filters: Any, **_: Any) -> dict[str, str]:  # noqa: ANN401
        return {f"v-{r}": r for r in readable} if user_id == "user-1" else {}

    async def candidates(org_id: str, *, pushdown: dict, scan_cap: int, **_: Any) -> RecordCandidates:  # noqa: ANN401
        ids = sorted(set.intersection(*(set(r) for r in pushdown.get("records", [[]]))))
        keys = [CandidateKey(i, 100_000 - int(i[1:])) for i in ids][:scan_cap]
        return RecordCandidates(keys, capped=len(keys) >= scan_cap)

    async def rows(org_id: str, *, record_ids: list[str], **_: Any) -> list[dict]:  # noqa: ANN401
        return [{"_key": i, "recordName": f"Doc {i}", "recordType": "FILE", "connectorName": "JIRA",
                 "connectorId": APP, "virtualRecordId": f"v-{i}", "people": [], "organizations": [], "links": []}
                for i in record_ids]

    graph.get_records_for_named_entities = AsyncMock(side_effect=named)
    graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=accessible)
    graph.get_record_query_candidates = AsyncMock(side_effect=candidates)
    graph.get_record_query_rows = AsyncMock(side_effect=rows)
    graph.readable = set(readable)
    graph.searched = list(searched)
    return graph


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    async def admitted(graph: MagicMock, context: object, keys: Any) -> frozenset[str]:  # noqa: ANN401
        return frozenset(k for k in keys if k in graph.readable)

    monkeypatch.setattr(engine, "admitted_node_ids", admitted)
    monkeypatch.setattr(recall, "admitted_node_ids", admitted)
    monkeypatch.setattr(op, "load_entity_access_context", AsyncMock(return_value=CONTEXT))
    monkeypatch.setattr(op, "cite_listed_records", AsyncMock(return_value={}))
    monkeypatch.setattr(op, "_search", lambda state, context, passages=None: AsyncMock(
        return_value=list(state["graph_provider"].searched)))


def _state(graph: MagicMock) -> dict[str, Any]:
    policy = KnowledgeGraphPolicy(mode=KnowledgeGraphMode.FULL, named_entities=True, record_query=True)
    return {"graph_provider": graph, "org_id": "org", "user_id": "user-1", KNOWLEDGE_GRAPH_POLICY_STATE_KEY: policy}


async def _find(graph: MagicMock, **mention: Any) -> tuple[bool, str]:  # noqa: ANN401
    return await op.execute_find_records(_state(graph), where=[{"mentions": mention}])


def _assert_hidden(reply: tuple[bool, str], unknown: tuple[bool, str]) -> None:
    assert reply == unknown
    text = reply[1]
    assert "approximate" not in text and "at least" not in text and "cursor=" not in text
    assert "m0" not in text and "Possible matches" not in text


_VALUES = [
    {"amount": {"from": 50_000, "currency": "USD"}},
    {"percent": {"from": 0.15}},
    {"name": "Pinecrest", "kind": "organization"},
]


@pytest.mark.parametrize("mention", _VALUES)
async def test_a_value_too_broad_across_the_org_that_no_readable_record_mentions_reads_as_unmentioned(
    mention: dict,
) -> None:
    unknown = await _find(_graph(0), **mention)
    _assert_hidden(await _find(_graph(_BROAD), **mention), unknown)


@pytest.mark.parametrize("mention", _VALUES)
async def test_a_scan_stopped_among_unreadable_mentions_reads_as_unmentioned(mention: dict) -> None:
    # As many mentions as the scan reads: the provider reports the scan capped.
    unknown = await _find(_graph(0), **mention)
    _assert_hidden(await _find(_graph(SCAN_CAP), **mention), unknown)


async def test_the_old_name_answers_an_unreadable_value_as_an_unmentioned_one() -> None:
    for mentioned in (_BROAD, SCAN_CAP):
        unknown = await merged_listings.find_records_for_value(_state(_graph(0)), amount_min=50_000, currency="USD")
        reply = await merged_listings.find_records_for_value(
            _state(_graph(mentioned)), amount_min=50_000, currency="USD",
        )
        _assert_hidden(reply, unknown)


async def test_search_hits_the_user_cannot_read_are_no_possible_matches(monkeypatch: pytest.MonkeyPatch) -> None:
    judge = AsyncMock()
    monkeypatch.setattr(op, "_judge", judge)
    unknown = await _find(_graph(0, searched=["m00001", "m00002"]), name="Pinecrest")
    _assert_hidden(await _find(_graph(3, searched=["m00001", "m00002"]), name="Pinecrest"), unknown)
    judge.assert_not_awaited()


async def test_a_made_up_cursor_reads_as_it_does_for_an_unmentioned_value() -> None:
    first = await _find(_graph(60, readable={f"m{i:05}" for i in range(60)}), name="Pinecrest")
    cursor = first[1].split('cursor="', 1)[1].split('"', 1)[0]
    for mentioned in (3, _BROAD):
        unknown = await op.execute_find_records(_state(_graph(0)), where=[{"mentions": {"name": "Pinecrest"}}],
                                                cursor=cursor)
        reply = await op.execute_find_records(_state(_graph(mentioned)), where=[{"mentions": {"name": "Pinecrest"}}],
                                              cursor=cursor)
        _assert_hidden(reply, unknown)


async def test_a_readable_mention_of_a_value_too_broad_across_the_org_is_counted() -> None:
    target = f"m{SCAN_CAP + 100:05}"
    ok, text = await _find(_graph(_BROAD, readable={target}), name="Pinecrest")
    assert ok and text.startswith("Total: 1 matching records you can open (exact)") and target in text


async def test_a_value_too_broad_among_readable_records_says_how_to_narrow_it() -> None:
    graph = _graph(_BROAD, readable={f"m{i:05}" for i in range(_BROAD)})
    assert await _find(graph, name="Pinecrest") == (False, TOO_BROAD_MENTION_MSG)


async def test_a_scan_stopped_beside_readable_matches_is_still_approximate() -> None:
    ok, text = await _find(_graph(SCAN_CAP, readable={"m00007"}), name="Pinecrest")
    assert ok and text.startswith("Total: at least 1 matching records you can open (approximate")
