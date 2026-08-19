"""The env knobs the load-test A/B depends on.

Each of these is read at process start, so a run that sets them and sees no
change would be a silent wasted experiment.
"""

from __future__ import annotations

import pytest

from app.agents.agent_loop.answer_streamer import answer_delta_min_interval
from app.modules.transformers.blob_storage import (
    COMPRESSION_THRESHOLD_BYTES_DEFAULT,
    compression_threshold_bytes,
)
from app.services.cache.accessible_records_cache import AccessibleRecordsCache


class TestAccessibleRecordsCacheSwitch:
    @pytest.mark.parametrize("value", ["off", "OFF", "false", "0", "no"])
    def test_disabling_values(self, monkeypatch, value) -> None:
        from app.services.cache.accessible_records_cache import _cache_enabled_from_env

        monkeypatch.setenv(AccessibleRecordsCache.ENV_ENABLED, value)
        assert _cache_enabled_from_env() is False

    @pytest.mark.parametrize("value", ["on", "1", "true", "yes"])
    def test_enabling_values(self, monkeypatch, value) -> None:
        from app.services.cache.accessible_records_cache import _cache_enabled_from_env

        monkeypatch.setenv(AccessibleRecordsCache.ENV_ENABLED, value)
        assert _cache_enabled_from_env() is True

    def test_default_is_on(self, monkeypatch) -> None:
        from app.services.cache.accessible_records_cache import _cache_enabled_from_env

        monkeypatch.delenv(AccessibleRecordsCache.ENV_ENABLED, raising=False)
        assert _cache_enabled_from_env() is True


class TestAnswerDeltaInterval:
    def test_default_is_250ms(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_ANSWER_DELTA_INTERVAL_MS", raising=False)
        assert answer_delta_min_interval() == 0.25

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ANSWER_DELTA_INTERVAL_MS", "100")
        assert answer_delta_min_interval() == 0.1

    def test_zero_restores_per_token_emits(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ANSWER_DELTA_INTERVAL_MS", "0")
        assert answer_delta_min_interval() == 0.0

    def test_garbage_falls_back_to_the_default(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ANSWER_DELTA_INTERVAL_MS", "fast")
        assert answer_delta_min_interval() == 0.25


class TestCompressionThreshold:
    def test_default(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_RECORD_COMPRESSION_THRESHOLD_BYTES", raising=False)
        assert compression_threshold_bytes() == COMPRESSION_THRESHOLD_BYTES_DEFAULT

    def test_env_override(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_RECORD_COMPRESSION_THRESHOLD_BYTES", "1048576")
        assert compression_threshold_bytes() == 1048576
