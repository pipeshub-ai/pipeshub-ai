"""Which duplicate decides a record's fate.

Under ``single`` every record resolves to one collection, so any duplicate is
*the* duplicate and the ordering below cannot be observed. Under
``per_connector_type`` it is the whole game: a Slack copy of a file has left
nothing behind in the Drive collection, so it can neither stand in for the
missing vectors nor be worth waiting on.

The failure this guards is order dependence. Picking the first duplicate of any
status and only then asking which collection it belongs to makes the answer
depend on the order the graph happened to return rows in — a different
collection's copy arriving first would send a record off to re-index content
its own collection already holds, and would let two records index the same
content into one collection at once.
"""

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.events.dedup import DedupDecision, DuplicateMatch, select_duplicate

DRIVE = "drive_records"
SLACK = "slack_records"


def _rec(key: str, status: str, collection: str, vrid: str | None = "vr-1") -> dict:
    return {
        "_key": key,
        "indexingStatus": status,
        "virtualRecordId": vrid,
        "_collection": collection,
    }


def _resolver(record) -> str | None:
    return record.get("_collection")


def _select(duplicates, current=DRIVE):
    return select_duplicate(duplicates, current, _resolver)


COMPLETED = ProgressStatus.COMPLETED.value
IN_PROGRESS = ProgressStatus.IN_PROGRESS.value
EMPTY = ProgressStatus.EMPTY.value
FAILED = ProgressStatus.FAILED.value


class TestPriorityOrder:
    def test_same_collection_finished_wins_over_everything(self):
        match = _select(
            [
                _rec("other-done", COMPLETED, SLACK),
                _rec("same-inflight", IN_PROGRESS, DRIVE),
                _rec("same-done", COMPLETED, DRIVE),
            ]
        )
        assert match.record["_key"] == "same-done"
        assert match.same_collection and match.is_processed

    def test_same_collection_finished_wins_even_when_listed_last(self):
        """The order-dependence bug: a different collection's copy arriving
        first must not decide the outcome."""
        match = _select(
            [_rec("other-done", COMPLETED, SLACK), _rec("same-done", COMPLETED, DRIVE)]
        )
        assert match.record["_key"] == "same-done"

    def test_same_collection_inflight_beats_other_collection_finished(self):
        """Waiting for the collection's own work beats re-indexing content it
        is already producing."""
        match = _select(
            [
                _rec("other-done", COMPLETED, SLACK),
                _rec("same-inflight", IN_PROGRESS, DRIVE),
            ]
        )
        assert match.record["_key"] == "same-inflight"
        assert match.same_collection and not match.is_processed

    def test_other_collection_finished_used_when_nothing_local(self):
        match = _select([_rec("other-done", COMPLETED, SLACK)])
        assert match.record["_key"] == "other-done"
        assert not match.same_collection and match.is_processed

    def test_other_collection_inflight_is_the_last_resort(self):
        match = _select([_rec("other-inflight", IN_PROGRESS, SLACK)])
        assert match.record["_key"] == "other-inflight"
        assert not match.same_collection and not match.is_processed

    def test_finished_beats_inflight_within_the_same_collection(self):
        match = _select(
            [_rec("same-inflight", IN_PROGRESS, DRIVE), _rec("same-done", COMPLETED, DRIVE)]
        )
        assert match.record["_key"] == "same-done"

    def test_finished_beats_inflight_within_the_other_collection(self):
        match = _select(
            [
                _rec("other-inflight", IN_PROGRESS, SLACK),
                _rec("other-done", COMPLETED, SLACK),
            ]
        )
        assert match.record["_key"] == "other-done"


class TestWhatCountsAsUsable:
    def test_an_empty_duplicate_counts_as_finished(self):
        """EMPTY genuinely produced no vectors — reusing it means this record
        is empty too, not that indexing was skipped by mistake."""
        match = _select([_rec("same-empty", EMPTY, DRIVE, vrid=None)])
        assert match.is_processed

    def test_a_completed_duplicate_without_a_vrid_is_not_reusable(self):
        """Nothing to reuse and nothing to point at."""
        assert _select([_rec("no-vrid", COMPLETED, DRIVE, vrid=None)]) is None

    def test_failed_duplicates_are_ignored(self):
        assert _select([_rec("failed", FAILED, DRIVE)]) is None

    def test_no_duplicates_is_no_match(self):
        assert _select([]) is None

    def test_none_entries_are_skipped(self):
        match = _select([None, _rec("same-done", COMPLETED, DRIVE)])
        assert match.record["_key"] == "same-done"


class TestUnresolvableCollections:
    def test_a_duplicate_whose_collection_is_unknown_counts_as_elsewhere(self):
        """Treating it as local would skip indexing on an unproven match, and
        that is the one outcome with no repair path."""
        match = select_duplicate(
            [_rec("unknown", COMPLETED, SLACK)], DRIVE, lambda r: None
        )
        assert not match.same_collection

    def test_an_unresolvable_current_collection_matches_nothing_locally(self):
        match = select_duplicate([_rec("done", COMPLETED, DRIVE)], None, _resolver)
        assert not match.same_collection


class TestSingleCollectionDegeneratesToTodaysBehaviour:
    """Every record resolves to one collection, so any duplicate is local."""

    def test_any_finished_duplicate_is_a_same_collection_match(self):
        match = select_duplicate(
            [_rec("a", COMPLETED, "records"), _rec("b", COMPLETED, "records")],
            "records",
            _resolver,
        )
        assert match.same_collection and match.is_processed

    def test_inflight_duplicate_is_a_same_collection_match(self):
        match = select_duplicate(
            [_rec("a", IN_PROGRESS, "records")], "records", _resolver
        )
        assert match.same_collection and not match.is_processed


class TestDedupDecisionDefaults:
    def test_defaults_to_index_with_no_reused_identity(self):
        decision = DedupDecision()
        assert decision.virtual_record_id is None
        assert decision.skip_indexing is False

    def test_is_immutable(self):
        """The decision is read after the fact to set the record's VRID;
        a mutable one could be changed between decision and use."""
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            DedupDecision().skip_indexing = True

    def test_match_is_immutable(self):
        import dataclasses

        match = DuplicateMatch(record={}, same_collection=True, is_processed=True)
        with pytest.raises(dataclasses.FrozenInstanceError):
            match.same_collection = False
