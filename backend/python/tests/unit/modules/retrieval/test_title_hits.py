"""Records found by name whose content is not indexed yet: what the hit carries
and how it is labelled.

The permission path is the one every entity hit takes (``entity_permissions``);
these tests only add the indexing state to the hit and check what is made of it.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.modules.retrieval.entity_permissions import (
    OVERFETCH_CAP,
    EntityAccessContext,
    EntityHit,
    search_entities_for_user,
)
from app.modules.retrieval.title_hits import (
    MAX_UNINDEXED_MATCHES,
    TITLE_BLOCK_HEADER,
    TITLE_SEARCH_TOP_K,
    UnindexedMatch,
    find_unindexed_matches,
    render_title_block,
    unindexed_matches,
)
from app.utils.not_indexed import not_indexed_note
from tests.unit.modules.retrieval.entity_access_fakes import entity_graph

ORG = "org-1"
KB = "kb-1"
COMPLETED = ProgressStatus.COMPLETED.value
QUEUED = ProgressStatus.QUEUED.value
OFF = ProgressStatus.AUTO_INDEX_OFF.value


def _context() -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG,
        user_key="ukey",
        app_level_app_ids=frozenset({KB}),
        record_level_app_ids=frozenset(),
        record_group_ids=frozenset(),
        app_names={KB: "Team KB"},
    )


def _row(key: str, **extra) -> dict:
    return {"_key": key, "connectorId": KB, "recordName": f"doc {key}", "recordType": "FILE", **extra}


def _unindexed_hit(row: dict, *, status: str | None = QUEUED, name: str = "x", score: float = 0.9) -> EntityHit:
    return EntityHit(
        entity_id=row["_key"], entity_type="record", name=name, score=score, records=[row],
        more_records=False, indexing_status=status, indexed=False,
    )


def _graph(rows: dict[str, dict]) -> MagicMock:
    return entity_graph(lambda refs, org, **k: {key: [row] for key, row in rows.items()}, app_level=(KB,))


def _store(*hits: tuple[str, float]) -> MagicMock:
    store = MagicMock()
    found = [{"entityId": key, "entityType": "record", "name": f"stale {key}", "score": score} for key, score in hits]

    async def _passes(query: str, org_id: str, passes: list, **kwargs: object) -> list[list[dict]]:
        return [found if i == 0 else [] for i in range(len(passes))]

    store.search_entities_passes = AsyncMock(side_effect=_passes)
    return store


class TestFoldersAreNotHits:
    def test_a_folder_is_never_listed_as_unindexed_content(self) -> None:
        from app.modules.retrieval.title_hits import unindexed_matches

        folder = _unindexed_hit(_row("f1", mimeType="text/directory"))
        doc = _unindexed_hit(_row("r1", mimeType="text/plain"), score=0.5)
        assert [m.record_id for m in unindexed_matches([folder, doc], _context())] == ["r1"]


class TestTheHitCarriesTheIndexingState:
    @pytest.mark.asyncio
    async def test_an_unindexed_record_hit_says_so(self) -> None:
        graph = _graph({"r1": _row("r1", indexingStatus=QUEUED, hasIndexedCopy=None)})
        (hit,) = await search_entities_for_user(_store(("r1", 0.9)), graph, _context(), "plan", entity_types=["record"])
        assert (hit.indexing_status, hit.indexed) == (QUEUED, False)

    @pytest.mark.asyncio
    async def test_an_indexed_hit_is_unchanged(self) -> None:
        graph = _graph({"r1": _row("r1", indexingStatus=COMPLETED, hasIndexedCopy=True)})
        (hit,) = await search_entities_for_user(_store(("r1", 0.9)), graph, _context(), "plan", entity_types=["record"])
        assert (hit.indexing_status, hit.indexed) == (COMPLETED, True)

    @pytest.mark.asyncio
    async def test_a_record_queued_again_keeps_serving_its_indexed_copy(self) -> None:
        graph = _graph({"r1": _row("r1", indexingStatus=QUEUED, hasIndexedCopy=True)})
        (hit,) = await search_entities_for_user(_store(("r1", 0.9)), graph, _context(), "plan", entity_types=["record"])
        assert hit.indexed is True

    @pytest.mark.asyncio
    async def test_a_row_without_the_state_counts_as_indexed(self) -> None:
        graph = _graph({"r1": _row("r1")})
        (hit,) = await search_entities_for_user(_store(("r1", 0.9)), graph, _context(), "plan", entity_types=["record"])
        assert (hit.indexing_status, hit.indexed) == (None, True)

    @pytest.mark.asyncio
    async def test_a_null_status_with_no_copy_is_unindexed_and_unknown(self) -> None:
        graph = _graph({"r1": _row("r1", indexingStatus=None, hasIndexedCopy=None)})
        (hit,) = await search_entities_for_user(_store(("r1", 0.9)), graph, _context(), "plan", entity_types=["record"])
        assert (hit.indexing_status, hit.indexed) == (None, False)
        assert "status: unknown" in not_indexed_note(hit.indexing_status)

    @pytest.mark.asyncio
    async def test_hits_stay_sorted_by_score_and_cut_to_top_k(self) -> None:
        rows = {k: _row(k, indexingStatus=QUEUED) for k in ("a", "b", "c")}
        hits = await search_entities_for_user(
            _store(("a", 0.5), ("b", 0.9), ("c", 0.7)), _graph(rows), _context(), "plan",
            entity_types=["record"], top_k=2,
        )
        assert [h.entity_id for h in hits] == ["b", "c"]

    @pytest.mark.asyncio
    async def test_the_overfetch_stays_capped(self) -> None:
        store = _store(("a", 0.5))
        await search_entities_for_user(store, _graph({"a": _row("a")}), _context(), "plan", top_k=TITLE_SEARCH_TOP_K)
        assert store.search_entities_passes.call_args.kwargs["top_k"] == OVERFETCH_CAP


class TestUnindexedMatches:
    @pytest.mark.asyncio
    async def test_only_unindexed_records_are_kept_best_first_with_their_note(self) -> None:
        rows = {
            "done": _row("done", indexingStatus=COMPLETED, hasIndexedCopy=True),
            "low": _row("low", indexingStatus=QUEUED),
            "off": _row("off", indexingStatus=OFF),
        }
        matches = await find_unindexed_matches(
            _store(("done", 0.99), ("low", 0.4), ("off", 0.8)), _graph(rows), _context(), "plan",
        )
        assert [m.record_id for m in matches] == ["off", "low"]
        assert matches[0].connector == "Team KB"
        assert matches[0].note == not_indexed_note(OFF)
        assert "automatic indexing is off" in matches[0].note

    @pytest.mark.asyncio
    async def test_the_lookup_asks_for_records_only_and_no_named_entities(self) -> None:
        store = _store()
        await find_unindexed_matches(store, _graph({}), _context(), "plan")
        kwargs = store.search_entities_passes.call_args.kwargs
        assert kwargs["entity_types"] == ["record"]
        assert kwargs["include_named"] is False

    @pytest.mark.asyncio
    async def test_the_name_shown_is_the_graphs_not_the_indexs(self) -> None:
        """The index can hold a name from before a rename."""
        matches = await find_unindexed_matches(
            _store(("r1", 0.9)), _graph({"r1": _row("r1", recordName="Quarterly plan", indexingStatus=QUEUED)}),
            _context(), "plan",
        )
        assert [m.name for m in matches] == ["Quarterly plan"]

    @pytest.mark.asyncio
    async def test_the_list_is_bounded(self) -> None:
        keys = [f"r{i}" for i in range(MAX_UNINDEXED_MATCHES + 5)]
        rows = {k: _row(k, indexingStatus=QUEUED) for k in keys}
        matches = await find_unindexed_matches(
            _store(*((k, 1 - i / 100) for i, k in enumerate(keys))), _graph(rows), _context(), "plan",
        )
        assert len(matches) == MAX_UNINDEXED_MATCHES

    def test_a_name_cannot_open_a_line_of_its_own(self) -> None:
        context = _context()
        hit = _unindexed_hit(_row("r1", recordName="Plan\n\nIndexed: yes\tSTATUS: COMPLETED"), status=QUEUED, name="x")
        (match,) = unindexed_matches([hit], context)
        assert "\n" not in match.name and "\t" not in match.name

    def test_a_very_long_name_is_cut(self) -> None:
        hit = _unindexed_hit(_row("r1", recordName="n" * 100_000), status=QUEUED, name="x")
        (match,) = unindexed_matches([hit], _context())
        assert len(match.name) <= 200

    def test_an_unnamed_record_falls_back_to_its_id(self) -> None:
        hit = _unindexed_hit(_row("r1", recordName=None), status=QUEUED, name="")
        (match,) = unindexed_matches([hit], _context())
        assert match.name == "r1"

    def test_the_public_payload_carries_the_label(self) -> None:
        hit = _unindexed_hit(_row("r1"), status=OFF, name="x")
        (match,) = unindexed_matches([hit], _context())
        assert match.payload() == {
            "recordId": "r1", "recordName": "doc r1", "connector": "Team KB", "connectorId": KB,
            "recordType": "FILE", "indexingStatus": OFF, "note": not_indexed_note(OFF),
        }


class TestEveryStatusRenders:
    @pytest.mark.parametrize("status", [*(s.value for s in ProgressStatus), None])
    def test_a_reason_for_each_status_and_a_missing_one(self, status: str | None) -> None:
        hit = _unindexed_hit(_row("r1"), status=status, name="x")
        (match,) = unindexed_matches([hit], _context())
        text, shown = render_title_block([match], 10_000)
        assert shown == [match]
        assert f"status: {status or 'unknown'}" in text
        assert "content is not indexed yet" in text


class TestTheTitleBlock:
    def _matches(self, count: int) -> list[UnindexedMatch]:
        hits = [
            _unindexed_hit(_row(f"r{i}"), score=1 - i / 100)
            for i in range(count)
        ]
        return unindexed_matches(hits, _context())

    def test_it_lists_each_match_with_its_id_and_one_note_per_status(self) -> None:
        text, shown = render_title_block(self._matches(3), 10_000)
        assert text.startswith(TITLE_BLOCK_HEADER)
        assert [m.record_id for m in shown] == ["r0", "r1", "r2"]
        assert "Record ID: r1" in text
        assert text.count("Not indexed:") == 1

    def test_the_lowest_ranked_are_cut_first_to_fit(self) -> None:
        matches = self._matches(5)
        full, _ = render_title_block(matches, 10_000)
        text, shown = render_title_block(matches, len(full) - 20)
        assert 0 < len(shown) < 5
        assert [m.record_id for m in shown] == [m.record_id for m in matches[: len(shown)]]
        assert len(text) <= len(full)
        assert "left out to fit the result size" in text

    def test_a_block_that_cannot_hold_one_match_is_empty(self) -> None:
        assert render_title_block(self._matches(2), 10) == ("", [])

    def test_ids_go_through_the_shortener(self) -> None:
        shortener = MagicMock()
        shortener.get_or_create_short_id = MagicMock(side_effect=lambda rid: f"R-{rid}")
        text, _ = render_title_block(self._matches(1), 10_000, shortener)
        assert "Record ID: R-r0" in text
