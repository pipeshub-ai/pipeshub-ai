"""Unit tests for app.telemetry.modules.memory_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.memory_metrics import (
    observe_request_memory_delta,
    set_process_memory,
)


class TestSetProcessMemory:
    def test_sets_rss_and_vms_gauges_in_bytes(self):
        set_process_memory("query_service_memory_metrics_test", rss_mb=512.0, vms_mb=1024.0)

        text = METRICS_BACKEND.serialize()
        rss_line = next(
            line for line in text.splitlines()
            if "pipeshub_process_memory_bytes" in line
            and 'service="query_service_memory_metrics_test"' in line
            and 'kind="rss"' in line
        )
        vms_line = next(
            line for line in text.splitlines()
            if "pipeshub_process_memory_bytes" in line
            and 'service="query_service_memory_metrics_test"' in line
            and 'kind="vms"' in line
        )
        assert float(rss_line.rsplit(" ", 1)[-1]) == 512.0 * 1024 * 1024
        assert float(vms_line.rsplit(" ", 1)[-1]) == 1024.0 * 1024 * 1024

    def test_a_later_call_overwrites_the_gauge_value(self):
        """Gauges track the LAST sample, not a cumulative total."""
        set_process_memory("query_service_memory_metrics_overwrite", rss_mb=100.0, vms_mb=200.0)
        set_process_memory("query_service_memory_metrics_overwrite", rss_mb=300.0, vms_mb=400.0)

        text = METRICS_BACKEND.serialize()
        rss_line = next(
            line for line in text.splitlines()
            if "pipeshub_process_memory_bytes" in line
            and 'service="query_service_memory_metrics_overwrite"' in line
            and 'kind="rss"' in line
        )
        assert float(rss_line.rsplit(" ", 1)[-1]) == 300.0 * 1024 * 1024


class TestObserveRequestMemoryDelta:
    def test_records_a_positive_delta(self):
        observe_request_memory_delta("query_service", "memory_metrics_test_positive", 42.0)

        text = METRICS_BACKEND.serialize()
        assert any(
            "pipeshub_request_memory_delta_mb" in line
            and 'label="memory_metrics_test_positive"' in line
            for line in text.splitlines()
        )

    def test_negative_delta_is_clamped_to_zero(self):
        """Histograms require non-negative observations; a negative delta
        (e.g. GC ran mid-request) must not raise or be silently dropped."""
        observe_request_memory_delta("query_service", "memory_metrics_test_negative", -25.0)

        text = METRICS_BACKEND.serialize()
        count_line = next(
            line for line in text.splitlines()
            if "pipeshub_request_memory_delta_mb_count" in line
            and 'label="memory_metrics_test_negative"' in line
        )
        assert count_line.endswith(" 1.0")
