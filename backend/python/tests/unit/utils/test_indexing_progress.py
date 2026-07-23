"""Tests for app.utils.indexing_progress."""

import json

from app.config.constants.arangodb import IndexingStage, ProgressStatus
from app.utils.indexing_progress import (
    build_indexing_progress,
    build_indexing_substage_progress,
    format_indexing_progress_message,
    normalize_indexing_progress,
    stage_for_status,
)


class TestBuildIndexingProgress:
    def test_returns_stage_and_heartbeat(self):
        fields = build_indexing_progress(IndexingStage.EXTRACTING)
        assert fields["indexingStage"] == "EXTRACTING"
        assert isinstance(fields["lastActivityTimestamp"], int)
        assert fields["lastActivityTimestamp"] > 0

    def test_uses_provided_timestamp(self):
        fields = build_indexing_progress(IndexingStage.COMPLETED, timestamp=12345)
        assert fields == {
            "indexingStage": "COMPLETED",
            "lastActivityTimestamp": 12345,
            "indexingProgress": None,
        }

    def test_default_timestamp_is_fresh(self):
        a = build_indexing_progress(IndexingStage.INDEXING)["lastActivityTimestamp"]
        b = build_indexing_progress(IndexingStage.INDEXING)["lastActivityTimestamp"]
        assert b >= a


class TestStageForStatus:
    def test_active_and_terminal_statuses_map_to_stages(self):
        assert stage_for_status(ProgressStatus.QUEUED) is IndexingStage.QUEUED
        assert stage_for_status(ProgressStatus.IN_PROGRESS) is IndexingStage.EXTRACTING
        assert stage_for_status(ProgressStatus.COMPLETED) is IndexingStage.COMPLETED
        assert stage_for_status(ProgressStatus.FAILED) is IndexingStage.FAILED

    def test_terminal_skip_statuses_have_no_stage(self):
        # These are rendered directly from indexingStatus, so they get no stage.
        for status in (
            ProgressStatus.EMPTY,
            ProgressStatus.AUTO_INDEX_OFF,
            ProgressStatus.FILE_TYPE_NOT_SUPPORTED,
            ProgressStatus.NOT_STARTED,
            ProgressStatus.ENABLE_MULTIMODAL_MODELS,
            ProgressStatus.PAUSED,
        ):
            assert stage_for_status(status) is None


class TestBuildIndexingSubstageProgress:
    def test_returns_normalized_progress_metrics(self):
        progress = build_indexing_substage_progress(
            current=3,
            total=10,
            unit="chunks",
            phase="embedding",
        )
        assert progress == {
            "current": 3,
            "total": 10,
            "unit": "chunks",
            "phase": "embedding",
            "message": "Embedding chunk 3 of 10",
        }

    def test_clamps_current_to_total(self):
        progress = build_indexing_substage_progress(
            current=15,
            total=10,
            unit="chunks",
            phase="embedding",
        )
        assert progress["current"] == 10
        assert progress["total"] == 10


class TestFormatIndexingProgressMessage:
    def test_embedding_uses_chunks_copy(self):
        assert format_indexing_progress_message(
            phase="embedding", current=200, total=277, unit="chunks"
        ) == "Embedding chunk 200 of 277"

    def test_legacy_documents_unit_maps_to_chunks_copy(self):
        assert format_indexing_progress_message(
            phase="embedding", current=5, total=10, unit="documents"
        ) == "Embedding chunk 5 of 10"

    def test_extraction_uses_page_copy(self):
        assert format_indexing_progress_message(
            phase="extracting", current=2, total=8, unit="pages"
        ) == "Extracting page 2 of 8"


class TestNormalizeIndexingProgress:
    def test_none_passes_through(self):
        assert normalize_indexing_progress(None) is None

    def test_dict_passes_through(self):
        # ArangoDB stores the native object.
        value = {"current": 1, "total": 3, "unit": "pages", "phase": "extracting"}
        assert normalize_indexing_progress(value) is value

    def test_json_string_is_parsed(self):
        # Neo4j stores it as a JSON string; it must round-trip back to a dict.
        value = {"current": 2, "total": 4, "unit": "chunks", "phase": "embedding"}
        assert normalize_indexing_progress(json.dumps(value)) == value

    def test_invalid_string_returns_none(self):
        assert normalize_indexing_progress("not json") is None

    def test_non_object_json_returns_none(self):
        assert normalize_indexing_progress("[1, 2, 3]") is None
