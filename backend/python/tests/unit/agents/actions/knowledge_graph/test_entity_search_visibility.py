"""``search_by_entities`` shows a name's entity only to a user who can read one of its records.

These run the tool over the real linker, access probe and listing engine, with a
fake graph holding the nodes and their records. User U asks about a name N whose
only nodes U cannot read and must get exactly the reply N gets when no node
exists, whether the access probe finished, ran out of time, or an unreadable node
shares a learned alias with a readable one.
"""
from __future__ import annotations

import asyncio
import functools
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_filters, entity_search
from app.modules.named_entities.keys import named_entity_key
from app.modules.named_entities.linking import candidates as linking_candidates
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    probe_permitted_entities,
)
from app.services.graph_db.common.utils import EntityCandidateRows
from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

ORG = "org-1"
JIRA = "jira-1"
CONTEXT = EntityAccessContext(
    org_id=ORG,
    user_key="ukey",
    app_level_app_ids=frozenset(),
    record_level_app_ids=frozenset({JIRA}),
    record_group_ids=frozenset(),
    app_names={JIRA: "Jira"},
)
ORGANIZATION = "organization"
PROBE_SECONDS = 0.05


def _node(name: str, *aliases: str) -> dict[str, Any]:
    key = f"name:{ORGANIZATION}:{name.casefold()}"
    node = {"id": named_entity_key(ORG, ORGANIZATION, key), "kind": ORGANIZATION, "name": name, "normKey": key}
    if aliases:
        node["normalizedAliases"] = [key, *(f"name:{ORGANIZATION}:{a}" for a in aliases)]
    return node


def _rows(prefix: str, n: int) -> list[dict[str, Any]]:
    return [
        {"_key": f"{prefix}{i}", "recordName": f"Doc {prefix}{i}", "recordType": "FILE",
         "connectorId": JIRA, "virtualRecordId": f"v-{prefix}{i}"}
        for i in range(n)
    ]


def _graph(
    nodes: list[dict[str, Any]], records: dict[str, list[dict[str, Any]]], readable: set[str],
    *, stall: frozenset[str] = frozenset(),
) -> Any:  # noqa: ANN401 - provider mock
    """``stall``: node ids whose probe rounds past the first never answer in time."""
    by_id = {node["id"]: node for node in nodes}

    async def build(refs: list[dict], org: str, *, limit_per_entity: int, offset: int, **_: object) -> dict:
        if offset and any(ref["id"] in stall for ref in refs):
            await asyncio.sleep(1.0)
        return {
            (ref["type"], ref["id"]): EntityCandidateRows(records.get(ref["id"], [])[offset:offset + limit_per_entity])
            for ref in refs
        }

    graph = entity_graph(build, permitted=readable)

    async def by_ids(org: str, ids: list[str]) -> list[dict[str, Any]]:
        return [by_id[i] for i in ids if i in by_id]

    async def by_prefix(org: str, prefixes: list[tuple[str, str]], limit: int) -> list[dict[str, Any]]:
        return [
            {**n, "key": key} for kind, key in prefixes for n in nodes
            if n["kind"] == kind and n["normKey"].startswith(f"{key} ")
        ][:limit]

    async def by_alias(org: str, pairs: list[tuple[str, str]], limit: int = 3) -> list[dict[str, Any]]:
        rows = []
        for kind, key in pairs:
            hits = [n for n in nodes if n["kind"] == kind and key in (n.get("normalizedAliases") or [])
                    and n["normKey"] != key]
            rows += [{**n, "key": key} for n in hits[:limit]]
        return rows

    async def spellings(org: str, entity_ids: list[str], record_ids: list[str]) -> list[dict[str, Any]]:
        return [
            {"entityId": i, "recordId": r, "extractedNames": [by_id[i]["name"]]}
            for i in entity_ids if i in by_id for r in record_ids
            if any(row["_key"] == r for row in records.get(i, []))
        ]

    graph.get_named_entities_by_ids = AsyncMock(side_effect=by_ids)
    graph.find_named_entities_by_key_prefix = AsyncMock(side_effect=by_prefix)
    graph.find_named_entities_by_alias = AsyncMock(side_effect=by_alias)
    graph.get_named_entity_mentions_in_records = AsyncMock(side_effect=spellings)
    return graph


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(entity_search, "_named_entities_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(entity_filters, "get_entity_access_context", AsyncMock(return_value=CONTEXT))

    async def _search(state: dict[str, Any], query: str, **kwargs: Any) -> str:  # noqa: ANN401
        return kwargs["preamble"] + "PASSAGES " + " ".join(kwargs["scope_virtual_record_ids"])

    monkeypatch.setattr(entity_search, "execute_search", _search)
    real = linking_candidates.CandidateFinder
    # The real probe, with a deadline short enough for a stalled round to run past it.
    monkeypatch.setattr(entity_search, "CandidateFinder", lambda *a, **kw: real(
        *a, probe=functools.partial(probe_permitted_entities, deadline_seconds=PROBE_SECONDS), **kw,
    ))


async def _ask(graph: Any, *names: str) -> tuple[bool, str]:  # noqa: ANN401
    state = {"org_id": ORG, "user_id": "user-1", "graph_provider": graph, "query": "q"}
    reply = await entity_search.execute_search_by_entities(
        state, mentions=[{"name": name, "kind": ORGANIZATION} for name in names],
    )
    # A searched scope returns the search's text; an empty one returns (ok, text).
    return (True, reply) if isinstance(reply, str) else reply


GLOBEX = _node("Globex")
ACME = _node("Acme")


class TestCutShortProbe:
    async def test_a_name_whose_probe_ran_out_of_time_reads_as_an_unmentioned_name(self) -> None:
        unknown = await _ask(_graph([], {}, set()), "Globex")
        stalled = _graph([GLOBEX], {GLOBEX["id"]: _rows("g", 2000)}, set(), stall=frozenset({GLOBEX["id"]}))
        reply = await _ask(stalled, "Globex")
        assert reply == unknown
        assert "cut short" not in reply[1] and GLOBEX["id"] not in reply[1]

    async def test_a_readable_name_beside_one_whose_probe_ran_out_reads_as_beside_an_unmentioned_one(self) -> None:
        acme_rows = {ACME["id"]: _rows("a", 2)}
        alone = await _ask(_graph([ACME], acme_rows, {"a0", "a1"}), "Acme", "Globex")
        stalled = _graph(
            [ACME, GLOBEX], {**acme_rows, GLOBEX["id"]: _rows("g", 2000)}, {"a0", "a1"},
            stall=frozenset({GLOBEX["id"]}),
        )
        reply = await _ask(stalled, "Acme", "Globex")
        assert reply == alone
        assert "PASSAGES v-a0 v-a1" in reply[1] and "cut short" not in reply[1]

    async def test_a_readable_name_is_still_found_and_searched(self) -> None:
        ok, text = await _ask(_graph([ACME], {ACME["id"]: _rows("a", 2)}, {"a1"}), "Acme")
        assert ok and "Acme (organization); exact match; mentioned in 1 readable record." in text
        assert text.endswith("PASSAGES v-a1")


HOSPITALITY = _node("Pinecrest Hospitality", "pinecrest")
BANK = _node("Pinecrest Bank", "pinecrest")
SHORT = _node("Pinecrest")


class TestLearnedAliases:
    async def test_an_unreadable_node_sharing_the_alias_changes_nothing(self) -> None:
        records = {HOSPITALITY["id"]: _rows("h", 2), BANK["id"]: _rows("b", 2)}
        alone = await _ask(_graph([HOSPITALITY], records, {"h0"}), "Pinecrest")
        reply = await _ask(_graph([BANK, HOSPITALITY], records, {"h0"}), "Pinecrest")
        assert reply == alone
        assert "Pinecrest Hospitality (organization, same entity by a learned alias); exact match" in reply[1]
        assert "Bank" not in reply[1]

    async def test_an_unreadable_node_sharing_a_full_names_alias_changes_nothing(self) -> None:
        records = {HOSPITALITY["id"]: _rows("h", 2), BANK["id"]: _rows("b", 2), SHORT["id"]: _rows("s", 1)}
        readable = {"h0", "s0"}
        alone = await _ask(_graph([HOSPITALITY, SHORT], records, readable), "Pinecrest Hospitality")
        reply = await _ask(_graph([BANK, HOSPITALITY, SHORT], records, readable), "Pinecrest Hospitality")
        assert reply == alone
        assert "Pinecrest (organization, same entity by a learned alias)" in reply[1] and "Bank" not in reply[1]

    async def test_two_readable_nodes_sharing_the_alias_still_tie_neither(self) -> None:
        records = {HOSPITALITY["id"]: _rows("h", 2), BANK["id"]: _rows("b", 2)}
        _, text = await _ask(_graph([BANK, HOSPITALITY], records, {"h0", "b0"}), "Pinecrest")
        assert "learned alias" not in text


class TestCounts:
    async def test_unreadable_records_past_a_few_readable_ones_change_no_count(self) -> None:
        few = _rows("a", 3)
        alone = await _ask(_graph([ACME], {ACME["id"]: few}, {"a0", "a1", "a2"}), "Acme")
        crowded = _graph([ACME], {ACME["id"]: [*few, *_rows("x", 1500)]}, {"a0", "a1", "a2"})
        reply = await _ask(crowded, "Acme")
        assert reply == alone
        assert "mentioned in 3 readable records." in reply[1] and "only the newest were searched" not in reply[1]

    async def test_a_full_page_of_readable_records_still_says_there_may_be_more(self) -> None:
        rows = _rows("a", 150)
        _, text = await _ask(_graph([ACME], {ACME["id"]: rows}, {r["_key"] for r in rows}), "Acme")
        assert "mentioned in 100+ readable records." in text and "only the newest were searched" in text
