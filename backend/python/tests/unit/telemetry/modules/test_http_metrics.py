"""Unit tests for app.telemetry.modules.http_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.http_metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS


class TestHttpMetrics:
    def test_request_counter_labels_and_accumulation(self):
        HTTP_REQUESTS.inc("query_service", "/api/v1/hm", "GET", "200", "org-1", "a.io")
        HTTP_REQUESTS.inc("query_service", "/api/v1/hm", "GET", "200", "org-1", "a.io")

        text = METRICS_BACKEND.serialize()
        line = next(
            line
            for line in text.splitlines()
            if line.startswith("pipeshub_http_requests_total") and "/api/v1/hm" in line
        )
        assert 'service="query_service"' in line
        assert 'method="GET"' in line
        assert 'status="200"' in line
        assert 'org="org-1"' in line
        assert 'domain="a.io"' in line
        assert line.endswith(" 2.0")

    def test_duration_histogram_observes_latency(self):
        HTTP_REQUEST_DURATION.observe(
            "query_service", "/api/v1/hm-latency", "POST", value=0.2
        )

        text = METRICS_BACKEND.serialize()
        count_line = next(
            line
            for line in text.splitlines()
            if line.startswith("pipeshub_http_request_duration_seconds_count")
            and "/api/v1/hm-latency" in line
        )
        assert 'service="query_service"' in count_line
        assert 'method="POST"' in count_line
        assert count_line.endswith(" 1.0")
        # 0.2 falls in the 0.25 bucket but not the 0.1 bucket
        bucket_lines = [
            line
            for line in text.splitlines()
            if "duration_seconds_bucket" in line and "/api/v1/hm-latency" in line
        ]
        le_01 = next(line for line in bucket_lines if 'le="0.1"' in line)
        le_025 = next(line for line in bucket_lines if 'le="0.25"' in line)
        assert le_01.endswith(" 0.0")
        assert le_025.endswith(" 1.0")
