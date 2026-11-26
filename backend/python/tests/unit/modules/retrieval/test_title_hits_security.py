"""A record found by name is shown only to someone the graph says may read it.

The title index is a hint for the vector search, never a decision: its stored
membership can be stale (a moved record, a revoked grant), and its stored name
can be an old one. Every hit goes through the same graph permission check as
any entity hit, inside the turn's limit, and a failure of that check is an
error, never a hit. Content search is untouched.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import entity_filters
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    load_entity_access_context,
)
from app.modules.demo_data.chat import EXCLUDED_APP_IDS_STATE_KEY
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
)
from app.modules.retrieval.selection_scope import SelectionScope
from app.modules.retrieval.title_hits import (
    UnindexedMatch,
    find_unindexed_matches,
    render_title_block,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.record_visibility import aql_live_record
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

ORG = "org-1"
CONF = "conf-1"
KB = "kb-1"
SECRET = "Layoff plan Q3 (confidential)"


def _context(*, row_scopes: tuple[SelectionScope, ...] = (), record_groups=("g1",)) -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG,
        user_key="ukey",
        app_level_app_ids=frozenset({KB}),
        record_level_app_ids=frozenset({CONF}),
        record_group_ids=frozenset(record_groups),
        app_names={KB: "Team KB", CONF: "Confluence"},
        row_scopes=row_scopes,
    )


def _row(key: str = "rec-1", connector: str = CONF, name: str = SECRET) -> dict:
    return {
        "_key": key, "connectorId": connector, "recordName": name, "recordType": "FILE",
        "indexingStatus": "QUEUED", "hasIndexedCopy": None,
    }


def _store(*keys: str) -> MagicMock:
    """The title index, whatever it remembers: every pass answers with these records."""
    store = MagicMock()
    hits = [{"entityId": k, "entityType": "record", "name": "stale name", "score": 0.9 - i / 10}
            for i, k in enumerate(keys)]

    async def _passes(query: str, org_id: str, passes: list, **kwargs: object) -> list[list[dict]]:
        return [list(hits) for _ in passes]

    store.search_entities_passes = AsyncMock(side_effect=_passes)
    store.dense_similarities = AsyncMock(side_effect=lambda query, texts: [1.0] * len(texts))
    return store


def _graph(rows: dict[str, dict], **kwargs) -> MagicMock:
    return entity_graph(
        lambda refs, org, **k: {key: [row] for key, row in rows.items()}, app_level=(KB,), **kwargs,
    )


async def _find(
    store: MagicMock, graph: MagicMock, context: EntityAccessContext | None = None,
) -> list[UnindexedMatch]:
    return await find_unindexed_matches(store, graph, context or _context(), "layoff plan")


def _shown(matches: list[UnindexedMatch]) -> str:
    return render_title_block(matches, 10_000)[0] + repr([m.payload() for m in matches])


class TestWhoSeesTheTitle:
    @pytest.mark.asyncio
    async def test_a_user_the_graph_admits_finds_it_and_others_do_not(self) -> None:
        rows = {"rec-1": _row()}
        reader = await _find(_store("rec-1"), _graph(rows, permitted={"rec-1"}))
        other = await _find(_store("rec-1"), _graph(rows, permitted=()))

        assert [m.record_id for m in reader] == ["rec-1"]
        assert SECRET in _shown(reader)
        assert other == []
        assert SECRET not in _shown(other)

    @pytest.mark.asyncio
    async def test_a_grant_revoked_after_the_point_was_written_hides_it(self) -> None:
        """The point still names the record; the check decides, and it runs at search time."""
        graph = _graph({"rec-1": _row()}, permitted={"rec-1"}, denied={"rec-1"})
        store = _store("rec-1")

        assert await _find(store, graph) == []
        store.search_entities_passes.assert_awaited()
        graph.check_access.assert_awaited()

    @pytest.mark.asyncio
    async def test_a_record_moved_to_a_group_the_user_cannot_reach_is_hidden(self) -> None:
        """The stale point still lists the old group, so the group pass returns it;
        the graph no longer grants it."""
        store = _store("rec-1")
        graph = _graph({"rec-1": _row()}, permitted=())

        assert await _find(store, graph, _context(record_groups=("old-group",))) == []
        group_pass = store.search_entities_passes.call_args.args[2][0]
        assert "old-group" in group_pass.record_group_ids

    @pytest.mark.asyncio
    async def test_the_name_comes_from_the_graph_not_the_point(self) -> None:
        matches = await _find(_store("rec-1"), _graph({"rec-1": _row(name="Renamed")}, permitted={"rec-1"}))
        assert [m.name for m in matches] == ["Renamed"]
        assert "stale name" not in _shown(matches)


class TestExistenceOracle:
    @pytest.mark.asyncio
    async def test_a_denied_record_and_a_missing_one_answer_alike(self) -> None:
        denied = await _find(_store("rec-1"), _graph({"rec-1": _row()}, permitted=(), denied={"rec-1"}))
        missing = await _find(_store("rec-1"), _graph({}))
        nothing = await _find(_store(), _graph({}))

        assert denied == missing == nothing == []
        assert render_title_block(denied, 10_000) == render_title_block(missing, 10_000) == ("", [])


class TestTheTurnsLimit:
    @pytest.mark.asyncio
    async def test_only_records_inside_a_selection_are_found(self) -> None:
        scope = SelectionScope(records={"rec-in": ("vr-in", KB)})
        rows = {"rec-in": _row("rec-in", KB), "rec-out": _row("rec-out", KB)}
        matches = await _find(_store("rec-in", "rec-out"), _graph(rows), _context(row_scopes=(scope,)))
        assert [m.record_id for m in matches] == ["rec-in"]

    @pytest.mark.asyncio
    async def test_a_limit_that_resolves_to_no_scope_admits_nothing(self) -> None:
        rows = {"rec-1": _row("rec-1", KB)}
        assert await _find(_store("rec-1"), _graph(rows), _context(row_scopes=(SelectionScope(),))) == []

    @pytest.mark.asyncio
    async def test_hidden_demo_data_is_left_out_of_the_access_context(self, monkeypatch: pytest.MonkeyPatch) -> None:
        access = AsyncMock(return_value=_context())
        monkeypatch.setattr(entity_filters, "get_entity_access_context", access)
        state = {"graph_provider": MagicMock(), "org_id": ORG, "user_id": "u1",
                 EXCLUDED_APP_IDS_STATE_KEY: frozenset({"demo-1"})}

        await load_entity_access_context(state)

        assert access.await_args.kwargs["exclude_app_ids"] == frozenset({"demo-1"})

    @pytest.mark.asyncio
    async def test_a_record_of_a_hidden_demo_app_is_never_asked_of_the_graph(self) -> None:
        """The context lacks the demo app, so no ref carries its connector and the row is not a candidate."""
        rows = {"demo-rec": _row("demo-rec", "demo-1")}
        graph = _graph(rows)
        assert await _find(_store("demo-rec"), graph) == []
        refs = graph.get_permitted_entity_records.await_args.args[0]
        assert all("demo-1" not in ref["connectorIds"] for ref in refs)


class TestFailingClosed:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("call", ["get_permitted_entity_records", "check_access", "get_knowledge_hub_access_v3"])
    async def test_a_failed_check_is_an_error_and_never_a_hit(self, call: str) -> None:
        graph = _graph({"rec-1": _row("rec-1", KB)})
        setattr(graph, call, AsyncMock(side_effect=RuntimeError("db down")))
        with pytest.raises(EntityAccessError):
            await _find(_store("rec-1"), graph)

    @pytest.mark.asyncio
    async def test_a_failed_vector_search_is_an_error(self) -> None:
        store = MagicMock()
        store.search_entities_passes = AsyncMock(side_effect=RuntimeError("vector db down"))
        with pytest.raises(EntityAccessError):
            await _find(store, _graph({}))


class TestTrashedRecordsAreExcluded:
    """Both providers drop a record in the trash from a title lookup, on both queries."""

    @pytest.mark.parametrize("variable, kind", [("rec", "candidate"), ("one", "permitted")])
    def test_arango(self, variable: str, kind: str) -> None:
        provider = ArangoHTTPProvider(logger=MagicMock(), config_service=MagicMock())
        query = (
            provider._entity_candidate_records_aql("record", filter_record_types=False) if kind == "candidate"
            else provider._permitted_entity_records_aql("record", filter_record_types=False)
        )
        assert aql_live_record(variable) in query

    @pytest.mark.parametrize("kind", ["candidate", "permitted"])
    def test_neo4j(self, kind: str) -> None:
        provider = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
        query = (
            provider._entity_candidate_records_cypher("record") if kind == "candidate"
            else provider._permitted_entity_records_cypher("record")
        )
        assert "coalesce(rec.isDeleted, false) = false" in query
