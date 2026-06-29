"""Tests for app.utils.indexing_progress."""

from app.config.constants.arangodb import IndexingStage, ProgressStatus
from app.utils.indexing_progress import (
    build_indexing_progress,
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
