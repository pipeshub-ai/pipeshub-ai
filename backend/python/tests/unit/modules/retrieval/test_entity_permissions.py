"""Unit tests for ``app.modules.retrieval.entity_permissions``.

The invariant under test: stored entity membership only affects which
entities the vector search returns; every access decision comes from graph
calls, and any failure raises ``EntityAccessError`` instead of looking like
"no access".
"""
import base64
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.retrieval import entity_permissions as ep
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
    get_entity_access_context,
    list_accessible_entity_records,
    search_entities_for_user,
)

ORG = "org-1"
USER = "user-1"


def _b64(payload: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()


def _context(
    *,
    app_level=("kb-1",),
    record_level=("conf-1",),
    record_groups=("space-a",),
) -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG,
        user_key="ukey",
        app_level_app_ids=frozenset(app_level),
        record_level_app_ids=frozenset(record_level),
        record_group_ids=frozenset(record_groups),
        app_names={"kb-1": "Team KB", "conf-1": "Confluence", "s3-1": "S3"},
    )


def _row(key: str, connector_id: str, **extra) -> dict:
    return {"_key": key, "connectorId": connector_id, "recordName": f"doc {key}", **extra}


def _hit(entity_id: str, entity_type: str, score: float, **extra) -> dict:
    return {"entityId": entity_id, "entityType": entity_type, "name": entity_id, "score": score, **extra}


def _graph(candidates=None, permitted=None) -> MagicMock:
    graph = MagicMock()
    build = candidates or (lambda *a, **k: {})

    def _candidates(refs, org, **kwargs):
        """Adapt id-keyed test fixtures to the provider's (type, id) keys.

        The cases below care about which rows come back for an entity, not
        about the key shape, which the provider tests pin directly.
        """
        types = {str(ref["id"]): ref.get("type", "") for ref in refs}
        return {
            key if isinstance(key, tuple) else (types.get(key, ""), key): rows
            for key, rows in build(refs, org, **kwargs).items()
        }

    graph.get_entity_candidate_records = AsyncMock(side_effect=_candidates)
    graph.filter_nodes_with_permission_role = AsyncMock(return_value=set(permitted or ()))
    return graph


# ---------------------------------------------------------------------------
# get_entity_access_context
# ---------------------------------------------------------------------------


class TestGetEntityAccessContext:
    @pytest.mark.asyncio
    async def test_missing_org_fails_closed(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock()
        with pytest.raises(EntityAccessError):
            await get_entity_access_context({}, graph, org_id="", user_id=USER)
        graph.get_entity_access_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_user_raises(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock(return_value=None)
        with pytest.raises(EntityAccessError):
            await get_entity_access_context({}, graph, org_id=ORG, user_id=USER)

    @pytest.mark.asyncio
    async def test_provider_error_raises_access_error(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock(side_effect=RuntimeError("db down"))
        with pytest.raises(EntityAccessError):
            await get_entity_access_context({}, graph, org_id=ORG, user_id=USER)

    @pytest.mark.asyncio
    async def test_classifies_apps_and_caches_per_scope(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock(return_value={
            "user_key": "ukey",
            "apps": [
                {"id": "kb-1", "name": "Team KB", "type": "KB", "permissionModel": None},
                {"id": "s3-1", "name": "S3", "type": "S3", "permissionModel": "APP_LEVEL"},
                {"id": "conf-1", "name": "Confluence", "type": "CONFLUENCE", "permissionModel": "RECORD_LEVEL"},
                {"id": "drive-1", "name": "Drive", "type": "DRIVE"},
            ],
            "record_group_ids": ["space-a", ""],
        })
        state: dict = {}

        context = await get_entity_access_context(
            state, graph, org_id=ORG, user_id=USER, source_ids=["conf-1", "kb-1", "s3-1", "drive-1"],
        )
        again = await get_entity_access_context(
            state, graph, org_id=ORG, user_id=USER, source_ids=["drive-1", "s3-1", "kb-1", "conf-1"],
        )

        assert context.app_level_app_ids == {"kb-1", "s3-1"}
        assert context.record_level_app_ids == {"conf-1", "drive-1"}
        assert context.record_group_ids == {"space-a"}
        assert again is context
        graph.get_entity_access_context.assert_awaited_once_with(
            USER, ORG, ["conf-1", "drive-1", "kb-1", "s3-1"], exclude_app_ids=frozenset(),
        )

    @pytest.mark.asyncio
    async def test_strict_empty_scope_reaches_nothing_without_a_query(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock()

        context = await get_entity_access_context(
            {}, graph, org_id=ORG, user_id=USER, source_ids=[], strict=True,
        )

        assert context.app_ids == frozenset()
        assert context.record_group_ids == frozenset()
        graph.get_entity_access_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_strict_with_sources_queries_normally(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock(return_value={
            "user_key": "ukey", "apps": [{"id": "conf-1"}], "record_group_ids": [],
        })

        context = await get_entity_access_context(
            {}, graph, org_id=ORG, user_id=USER, source_ids=["conf-1"], strict=True,
        )

        assert context.app_ids == {"conf-1"}
        graph.get_entity_access_context.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_exclusions_are_forwarded_and_part_of_the_cache_key(self) -> None:
        graph = MagicMock()
        graph.get_entity_access_context = AsyncMock(return_value={
            "user_key": "ukey", "apps": [], "record_group_ids": [],
        })
        state: dict = {}

        await get_entity_access_context(state, graph, org_id=ORG, user_id=USER)
        await get_entity_access_context(
            state, graph, org_id=ORG, user_id=USER, exclude_app_ids={"demo-1", ""},
        )

        assert graph.get_entity_access_context.await_count == 2
        assert graph.get_entity_access_context.call_args.kwargs == {
            "exclude_app_ids": frozenset({"demo-1"}),
        }

    @pytest.mark.asyncio
    async def test_empty_context_searches_no_vectors(self) -> None:
        store = MagicMock()
        store.search_entities_passes = AsyncMock()
        context = await get_entity_access_context(
            {}, MagicMock(), org_id=ORG, user_id=USER, strict=True,
        )

        hits = await search_entities_for_user(store, MagicMock(), context, "legal")

        assert hits == []
        store.search_entities_passes.assert_not_called()


# ---------------------------------------------------------------------------
# list_accessible_entity_records
# ---------------------------------------------------------------------------


class TestListAccessibleEntityRecords:
    @pytest.mark.asyncio
    async def test_app_level_rows_skip_the_permission_check(self) -> None:
        graph = _graph(candidates=lambda refs, org, **k: {"t1": [_row("r1", "kb-1"), _row("r2", "kb-1")]})

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["r1", "r2"]
        assert page.next_cursor is None
        graph.filter_nodes_with_permission_role.assert_not_called()

    @pytest.mark.asyncio
    async def test_record_level_rows_are_checked_with_raise_on_error(self) -> None:
        graph = _graph(
            candidates=lambda refs, org, **k: {"t1": [_row("r1", "conf-1"), _row("r2", "conf-1")]},
            permitted={"r2"},
        )

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["r2"]
        _, kwargs = graph.filter_nodes_with_permission_role.call_args
        assert kwargs["raise_on_error"] is True
        nodes = graph.filter_nodes_with_permission_role.call_args.args[0]
        assert nodes == [{"id": "r1", "type": "record"}, {"id": "r2", "type": "record"}]

    @pytest.mark.asyncio
    async def test_rows_from_connectors_outside_scope_are_dropped(self) -> None:
        graph = _graph(candidates=lambda refs, org, **k: {"t1": [_row("r1", "other-app")]})

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic",
        )

        assert page.records == []

    @pytest.mark.asyncio
    async def test_cursor_points_after_last_row_when_limit_hit_mid_batch(self) -> None:
        rows = [_row(f"r{i}", "kb-1") for i in range(40)]
        graph = _graph(candidates=lambda refs, org, **k: {"t1": rows[k["offset"]:k["offset"] + k["limit_per_entity"]]})

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=3, cursor="5",
        )

        assert [r["_key"] for r in page.records] == ["r5", "r6", "r7"]
        assert page.next_cursor == "8"

    @pytest.mark.asyncio
    async def test_scan_cap_returns_scan_offset_cursor(self) -> None:
        denied = [_row(f"r{i}", "conf-1") for i in range(500)]
        graph = _graph(
            candidates=lambda refs, org, **k: {"t1": denied[k["offset"]:k["offset"] + k["limit_per_entity"]]},
            permitted=set(),
        )

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=5, max_scan=80,
        )

        assert page.records == []
        assert page.next_cursor == "80"

    @pytest.mark.asyncio
    async def test_unreachable_record_group_only_queries_app_level_connectors(self) -> None:
        graph = _graph(candidates=lambda refs, org, **k: {"rg-x": []})

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="rg-x", entity_type="record_group",
        )

        assert page.records == []
        refs = graph.get_entity_candidate_records.call_args.args[0]
        assert refs == [{"id": "rg-x", "type": "record_group", "connectorIds": ["kb-1"]}]

    @pytest.mark.asyncio
    async def test_reachable_record_group_queries_all_connectors(self) -> None:
        graph = _graph(candidates=lambda refs, org, **k: {"space-a": []})

        await list_accessible_entity_records(
            graph, _context(), entity_id="space-a", entity_type="record_group",
        )

        refs = graph.get_entity_candidate_records.call_args.args[0]
        assert refs[0]["connectorIds"] == ["conf-1", "kb-1"]

    @pytest.mark.asyncio
    async def test_permission_check_failure_raises(self) -> None:
        graph = _graph(candidates=lambda refs, org, **k: {"t1": [_row("r1", "conf-1")]})
        graph.filter_nodes_with_permission_role = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(EntityAccessError):
            await list_accessible_entity_records(
                graph, _context(), entity_id="t1", entity_type="topic",
            )

    @pytest.mark.asyncio
    async def test_candidate_failure_raises(self) -> None:
        graph = _graph()
        graph.get_entity_candidate_records = AsyncMock(side_effect=RuntimeError("db down"))

        with pytest.raises(EntityAccessError):
            await list_accessible_entity_records(
                graph, _context(), entity_id="t1", entity_type="topic",
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cursor",
        [
            "abc",
            "-1",
            _b64({"offset": -1}),
            _b64({"page": 2}),
            '{"offset": "50"}',
            '{"offset": true}',
            "eyJ!!!",
        ],
    )
    async def test_invalid_cursor_rejected(self, cursor) -> None:
        with pytest.raises(ValueError):
            await list_accessible_entity_records(
                _graph(), _context(), entity_id="t1", entity_type="topic", cursor=cursor,
            )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "cursor",
        ["5", " 5 ", '{"offset": 5}', _b64({"offset": 5})],
    )
    async def test_cursor_envelopes_resolve_to_the_same_offset(self, cursor) -> None:
        """Models reconstruct a cursor as an opaque base64 blob instead of
        copying the integer back. The offset is unambiguous either way, and
        every row it reaches is still permission-checked."""
        rows = [_row(f"r{i}", "kb-1") for i in range(20)]
        graph = _graph(
            candidates=lambda refs, org, **k: {
                "t1": rows[k["offset"]:k["offset"] + k["limit_per_entity"]]
            }
        )

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=2, cursor=cursor,
        )

        assert [r["_key"] for r in page.records] == ["r5", "r6"]

    @pytest.mark.asyncio
    async def test_invalid_cursor_message_names_the_recovery(self) -> None:
        with pytest.raises(ValueError, match="omit it to start from the first page"):
            await list_accessible_entity_records(
                _graph(), _context(), entity_id="t1", entity_type="topic", cursor="abc",
            )


# ---------------------------------------------------------------------------
# search_entities_for_user
# ---------------------------------------------------------------------------


def _store(*pass_results) -> MagicMock:
    """All passes go to the store in one call; it answers per pass."""
    store = MagicMock()

    async def _passes(query: str, org_id: str, passes: list, **kwargs: object) -> list[list[dict]]:
        return [list(pass_results[i]) if i < len(pass_results) else [] for i in range(len(passes))]

    store.search_entities_passes = AsyncMock(side_effect=_passes)
    return store


def _passes(store: MagicMock) -> list:
    return store.search_entities_passes.call_args.args[2]


class TestSearchEntitiesForUser:
    @pytest.mark.asyncio
    async def test_first_pass_uses_record_groups_and_app_level_connectors(self) -> None:
        store = _store([_hit("t1", "topic", 0.9)])
        graph = _graph(candidates=lambda refs, org, **k: {"t1": [_row("r1", "kb-1")]})

        hits = await search_entities_for_user(store, graph, _context(), "roadmap", top_k=1)

        assert [h.entity_id for h in hits] == ["t1"]
        first = _passes(store)[0]
        assert first.record_group_ids == {"space-a"}
        assert first.connector_ids == {"kb-1"}
        assert first.org_wide is False
        store.search_entities_passes.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_widens_to_record_level_then_org_wide_when_short(self) -> None:
        store = _store([], [], [_hit("t-empty", "topic", 0.8, connectorIds=[])])
        graph = _graph(candidates=lambda refs, org, **k: {"t-empty": [_row("r1", "conf-1")]}, permitted={"r1"})

        hits = await search_entities_for_user(store, graph, _context(), "roadmap", top_k=5)

        assert [h.entity_id for h in hits] == ["t-empty"]
        passes = _passes(store)
        assert passes[1].record_group_ids == set() and passes[1].connector_ids == {"conf-1"}
        assert passes[2].org_wide is True

    @pytest.mark.asyncio
    async def test_stale_app_level_membership_does_not_keep_an_entity(self) -> None:
        stale = _hit("t1", "topic", 0.9, connectorIds=["kb-1"])
        store = _store([stale], [], [])
        graph = _graph(candidates=lambda refs, org, **k: {"t1": []})

        hits = await search_entities_for_user(store, graph, _context(), "roadmap", top_k=5)

        assert hits == []

    @pytest.mark.asyncio
    async def test_seen_entities_are_not_re_evaluated_in_later_passes(self) -> None:
        store = _store([_hit("t1", "topic", 0.9)], [_hit("t1", "topic", 0.9)], [])
        graph = _graph(candidates=lambda refs, org, **k: {"t1": []})

        await search_entities_for_user(store, graph, _context(), "roadmap", top_k=5)

        assert graph.get_entity_candidate_records.await_count == 1

    @pytest.mark.asyncio
    async def test_one_candidate_and_one_permission_call_per_round(self) -> None:
        store = _store([
            _hit("t1", "topic", 0.9),
            _hit("rec-1", "record", 0.8),
            _hit("rg-x", "record_group", 0.7),
        ], [], [])
        graph = _graph(
            candidates=lambda refs, org, **k: {
                "t1": [_row("r1", "conf-1")],
                "rec-1": [_row("rec-1", "conf-1")],
                "rg-x": [],
            },
            permitted={"r1", "rec-1"},
        )

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=10)

        assert {h.entity_id for h in hits} == {"t1", "rec-1"}
        assert graph.get_entity_candidate_records.await_count == 1
        assert graph.filter_nodes_with_permission_role.await_count == 1
        refs = graph.get_entity_candidate_records.call_args.args[0]
        assert {"id": "rg-x", "type": "record_group", "connectorIds": ["kb-1"]} in refs

    @pytest.mark.asyncio
    async def test_reachable_record_group_kept_without_permitted_rows(self) -> None:
        store = _store([_hit("space-a", "record_group", 0.9)], [], [])
        graph = _graph(candidates=lambda refs, org, **k: {"space-a": []})

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=5)

        assert [h.entity_id for h in hits] == ["space-a"]
        graph.filter_nodes_with_permission_role.assert_not_called()

    @pytest.mark.asyncio
    async def test_undecided_probe_continues_into_next_round(self) -> None:
        first = [_row(f"d{i}", "conf-1") for i in range(ep.PROBE_BATCH)]
        store = _store([_hit("t1", "topic", 0.9)], [], [])
        graph = _graph(
            candidates=lambda refs, org, **k: {"t1": first if k["offset"] == 0 else [_row("ok", "conf-1")]},
            permitted={"ok"},
        )

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=5)

        assert [h.entity_id for h in hits] == ["t1"]
        assert graph.get_entity_candidate_records.await_count == 2

    @pytest.mark.asyncio
    async def test_permission_failure_fails_the_whole_search(self) -> None:
        store = _store([_hit("t1", "topic", 0.9)])
        graph = _graph(candidates=lambda refs, org, **k: {"t1": [_row("r1", "conf-1")]})
        graph.filter_nodes_with_permission_role = AsyncMock(side_effect=RuntimeError("down"))

        with pytest.raises(EntityAccessError):
            await search_entities_for_user(store, graph, _context(), "q", top_k=5)

    @pytest.mark.asyncio
    async def test_vector_failure_raises_access_error(self) -> None:
        store = MagicMock()
        store.search_entities_passes = AsyncMock(side_effect=RuntimeError("qdrant down"))

        with pytest.raises(EntityAccessError):
            await search_entities_for_user(store, _graph(), _context(), "q", top_k=5)

    @pytest.mark.asyncio
    async def test_too_many_record_groups_drops_group_clause(self, monkeypatch) -> None:
        monkeypatch.setattr(ep, "MAX_RG_FILTER_IDS", 1)
        store = _store([], [], [])
        context = _context(record_groups=("a", "b"))

        await search_entities_for_user(store, _graph(), context, "q", top_k=5)

        assert _passes(store)[0].record_group_ids == set()

    @pytest.mark.asyncio
    async def test_deadline_stops_further_passes(self, monkeypatch) -> None:
        monkeypatch.setattr(ep, "SEARCH_DEADLINE_SECONDS", -1.0)
        store = _store([], [], [])

        hits = await search_entities_for_user(store, _graph(), _context(), "q", top_k=5)

        assert hits == []
        store.search_entities_passes.assert_not_called()

    @pytest.mark.asyncio
    async def test_results_sorted_by_score_with_bounded_preview(self) -> None:
        store = _store([
            _hit("low", "topic", 0.2),
            _hit("high", "topic", 0.9),
        ], [], [])
        rows = [_row(f"r{i}", "kb-1") for i in range(5)]
        graph = _graph(candidates=lambda refs, org, **k: {"low": rows, "high": rows})

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=5)

        assert [h.entity_id for h in hits] == ["high", "low"]
        assert len(hits[0].records) == ep.PREVIEW_RECORD_COUNT
        assert hits[0].more_records is True

    @pytest.mark.asyncio
    async def test_user_without_apps_runs_no_search(self) -> None:
        store = _store()
        context = _context(app_level=(), record_level=(), record_groups=())

        hits = await search_entities_for_user(store, _graph(), context, "q", top_k=5)

        assert hits == []
        store.search_entities_passes.assert_not_called()


class TestReadPathRoundTrips:
    """KG-08 and KG-38: one vector request for every pass, the union of their
    hits probed together, and one deadline over the whole call."""

    @pytest.mark.asyncio
    async def test_all_passes_are_one_vector_request(self) -> None:
        store = _store([_hit("t1", "topic", 0.9)], [_hit("t2", "topic", 0.8)], [_hit("t3", "topic", 0.7)])
        graph = _graph(candidates=lambda refs, org, **k: {r["id"]: [_row("r", "kb-1")] for r in refs})

        await search_entities_for_user(store, graph, _context(), "q", top_k=10)

        store.search_entities_passes.assert_awaited_once()
        assert len(_passes(store)) == 3

    @pytest.mark.asyncio
    async def test_hits_of_every_pass_are_probed_in_one_round(self) -> None:
        store = _store([_hit("t1", "topic", 0.9)], [_hit("t2", "topic", 0.8)], [_hit("t1", "topic", 0.9)])
        graph = _graph(candidates=lambda refs, org, **k: {r["id"]: [_row("r", "kb-1")] for r in refs})

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=10)

        assert sorted(h.entity_id for h in hits) == ["t1", "t2"]
        assert graph.get_entity_candidate_records.await_count == 1
        refs = graph.get_entity_candidate_records.call_args.args[0]
        assert sorted(r["id"] for r in refs) == ["t1", "t2"]

    @pytest.mark.asyncio
    async def test_a_slow_probe_round_is_cut_at_the_deadline(self, monkeypatch) -> None:
        import asyncio

        monkeypatch.setattr(ep, "SEARCH_DEADLINE_SECONDS", 0.2)
        store = _store([_hit("t1", "topic", 0.9)])

        async def _slow(refs: list, org: str, **kwargs: object) -> dict:
            await asyncio.sleep(5)
            return {}

        graph = _graph()
        graph.get_entity_candidate_records = AsyncMock(side_effect=_slow)
        started = asyncio.get_running_loop().time()

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=5)

        assert hits == []
        assert asyncio.get_running_loop().time() - started < 2

    @pytest.mark.asyncio
    async def test_rounds_stop_once_the_deadline_has_passed(self, monkeypatch) -> None:
        """The first round's candidate query takes the call past its deadline:
        no permission check or further round follows."""
        clock = {"t": 0.0}
        monkeypatch.setattr(ep.time, "monotonic", lambda: clock["t"])
        first = [_row(f"d{i}", "conf-1") for i in range(ep.PROBE_BATCH)]

        def _slow_round(refs: list, org: str, **kwargs: object) -> dict:
            clock["t"] += ep.SEARCH_DEADLINE_SECONDS + 1
            return {"t1": first}

        store = _store([_hit("t1", "topic", 0.9)])
        graph = _graph(candidates=_slow_round)

        hits = await search_entities_for_user(store, graph, _context(), "q", top_k=5)

        assert hits == []
        assert graph.get_entity_candidate_records.await_count == 1
        graph.filter_nodes_with_permission_role.assert_not_awaited()


class TestListingDeadline:
    @pytest.mark.asyncio
    async def test_a_listing_past_its_deadline_returns_what_it_has_with_a_cursor(self, monkeypatch) -> None:
        clock = {"t": 0.0}
        monkeypatch.setattr(ep.time, "monotonic", lambda: clock["t"])
        batch = [_row(f"d{i}", "conf-1") for i in range(ep.CANDIDATE_BATCH_MIN)]

        def _slow_batch(refs: list, org: str, **kwargs: object) -> dict:
            clock["t"] += ep.LISTING_DEADLINE_SECONDS + 1
            return {"t1": batch}

        graph = _graph(candidates=_slow_batch, permitted={"d0"})

        page = await list_accessible_entity_records(
            graph, _context(), entity_id="t1", entity_type="topic", limit=5,
        )

        assert [r["_key"] for r in page.records] == ["d0"]
        assert page.next_cursor == str(ep.CANDIDATE_BATCH_MIN)
        assert graph.get_entity_candidate_records.await_count == 1
