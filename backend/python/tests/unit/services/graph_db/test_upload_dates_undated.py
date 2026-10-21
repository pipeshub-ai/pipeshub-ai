"""With content dates on, an upload dated only by its upload has no document date.

Its upload time is still the latest it can have been written, so a date filter
leaves it out only when its upload falls before an after-bound; newest-first
entity listings put it last. Both graph backends and the keyword search's own
date check follow the same rule.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.pattern_match import _graph_record_in_time_range

UPLOADED = 1792090099000  # 2026-10-15
EMAIL_DAY = int(datetime(2025, 2, 11, tzinfo=UTC).timestamp() * 1000)
FEBRUARY_2025 = {
    "source_created_after_ms": int(datetime(2025, 2, 1, tzinfo=UTC).timestamp() * 1000),
    "source_created_before_ms": int(datetime(2025, 2, 28, tzinfo=UTC).timestamp() * 1000),
}
SINCE_2026 = {"source_created_after_ms": int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1000)}
SINCE_2027 = {"source_created_after_ms": int(datetime(2027, 1, 1, tzinfo=UTC).timestamp() * 1000)}
BEFORE_2024 = {"source_created_before_ms": int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)}
EDITED_BEFORE_2024 = {"source_updated_before_ms": int(datetime(2024, 1, 1, tzinfo=UTC).timestamp() * 1000)}
EDITED_SINCE_2027 = {"source_updated_after_ms": int(datetime(2027, 1, 1, tzinfo=UTC).timestamp() * 1000)}


@pytest.fixture
def content_dates_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONTENT_DOCUMENT_DATES", "true")


def _neo4j_clause(time_range: dict | None) -> str:
    return Neo4jProvider._build_time_range_conditions(time_range, {}, "r")


def _arango_clause(time_range: dict | None) -> str:
    return ArangoHTTPProvider._append_source_created_time_filters("", time_range, {})


class TestDateFilters:
    def test_off_the_filters_are_unchanged(self) -> None:
        assert "sourceTimestampOrigin" not in _neo4j_clause(FEBRUARY_2025)
        assert "sourceTimestampOrigin" not in _arango_clause(FEBRUARY_2025)

    @pytest.mark.usefixtures("content_dates_on")
    def test_on_an_after_bound_still_reads_the_upload_time(self) -> None:
        assert _neo4j_clause(SINCE_2026) == "AND r.sourceCreatedAtTimestamp >= $sourceCreatedAfterMs"
        assert _arango_clause(SINCE_2026) == "FILTER record.sourceCreatedAtTimestamp >= @sourceCreatedAfterMs"

    @pytest.mark.usefixtures("content_dates_on")
    def test_on_a_before_bound_keeps_upload_dated_records(self) -> None:
        assert _neo4j_clause(BEFORE_2024) == (
            "AND (r.sourceCreatedAtTimestamp <= $sourceCreatedBeforeMs"
            " OR coalesce(r.sourceTimestampOrigin, '') = 'upload')"
        )
        assert _neo4j_clause(EDITED_BEFORE_2024) == (
            "AND (r.sourceLastModifiedTimestamp <= $sourceUpdatedBeforeMs"
            " OR coalesce(r.sourceTimestampOrigin, '') = 'upload')"
        )
        assert _arango_clause(BEFORE_2024) == (
            "FILTER (record.sourceCreatedAtTimestamp != null"
            " AND record.sourceCreatedAtTimestamp <= @sourceCreatedBeforeMs)"
            ' OR record.sourceTimestampOrigin == "upload"'
        )

    @pytest.mark.usefixtures("content_dates_on")
    def test_on_no_clause_leaves_every_upload_dated_record_out(self) -> None:
        for time_range in (SINCE_2026, FEBRUARY_2025, BEFORE_2024, EDITED_SINCE_2027):
            assert "<> 'upload'" not in _neo4j_clause(time_range)
            assert '!= "upload"' not in _arango_clause(time_range)

    @pytest.mark.usefixtures("content_dates_on")
    def test_on_a_search_without_dates_is_unchanged(self) -> None:
        assert _neo4j_clause(None) == ""
        assert _arango_clause({}) == ""

    @pytest.mark.usefixtures("content_dates_on")
    def test_keyword_hits_follow_the_same_rule(self) -> None:
        email = {"sourceCreatedAtTimestamp": EMAIL_DAY, "sourceTimestampOrigin": "content"}
        upload = {
            "sourceCreatedAtTimestamp": UPLOADED,
            "sourceLastModifiedTimestamp": UPLOADED,
            "sourceTimestampOrigin": "upload",
        }
        drive = {"sourceCreatedAtTimestamp": UPLOADED}
        assert _graph_record_in_time_range(email, FEBRUARY_2025) is True
        assert _graph_record_in_time_range(email, SINCE_2026) is False
        # Uploaded after each lower bound, so it may have been written in range.
        assert _graph_record_in_time_range(upload, SINCE_2026) is True
        assert _graph_record_in_time_range(upload, FEBRUARY_2025) is True
        assert _graph_record_in_time_range(upload, BEFORE_2024) is True
        assert _graph_record_in_time_range(upload, EDITED_BEFORE_2024) is True
        # Uploaded before the lower bound, so written before it too.
        assert _graph_record_in_time_range(upload, SINCE_2027) is False
        assert _graph_record_in_time_range(upload, EDITED_SINCE_2027) is False
        assert _graph_record_in_time_range(drive, SINCE_2026) is True
        assert _graph_record_in_time_range(drive, FEBRUARY_2025) is False

    def test_off_keyword_hits_match_upload_times(self) -> None:
        upload = {"sourceCreatedAtTimestamp": UPLOADED, "sourceTimestampOrigin": "upload"}
        assert _graph_record_in_time_range(upload, SINCE_2026) is True
        assert _graph_record_in_time_range(upload, BEFORE_2024) is False


class TestNewestFirst:
    def test_off_the_order_is_unchanged(self) -> None:
        neo4j = Neo4jProvider.__new__(Neo4jProvider)
        arango = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
        assert neo4j._entity_candidate_order == (
            "coalesce(rec.sourceLastModifiedTimestamp, rec.updatedAtTimestamp, 0) DESC, rec.id ASC"
        )
        assert arango._entity_candidate_sort == (
            "SORT NOT_NULL(r.sourceLastModifiedTimestamp, r.updatedAtTimestamp, 0) DESC, key ASC"
        )

    @pytest.mark.usefixtures("content_dates_on")
    def test_on_upload_dated_records_sort_as_undated(self) -> None:
        neo4j = Neo4jProvider.__new__(Neo4jProvider)
        arango = ArangoHTTPProvider.__new__(ArangoHTTPProvider)
        assert neo4j._entity_candidate_order.startswith(
            "CASE WHEN rec.sourceTimestampOrigin = 'upload' THEN 0 ELSE "
        )
        assert arango._entity_candidate_sort.startswith('SORT (r.sourceTimestampOrigin == "upload" ? 0 : ')


class TestTimeline:
    def test_off_an_upload_is_placed_by_its_upload_time(self) -> None:
        from app.agents.actions.knowledge_graph.ops.timeline import record_date_ms

        upload = {"sourceCreatedAtTimestamp": UPLOADED, "sourceTimestampOrigin": "upload"}
        assert record_date_ms(upload, "created") == UPLOADED

    @pytest.mark.usefixtures("content_dates_on")
    def test_on_an_upload_dated_only_by_its_upload_is_undated(self) -> None:
        from app.agents.actions.knowledge_graph.ops.timeline import record_date_ms

        upload = {"sourceCreatedAtTimestamp": UPLOADED, "sourceTimestampOrigin": "upload"}
        email = {"sourceCreatedAtTimestamp": EMAIL_DAY, "sourceTimestampOrigin": "content"}
        assert record_date_ms(upload, "created") is None
        assert record_date_ms(email, "created") == EMAIL_DAY
