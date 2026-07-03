"""Unit tests for app.telemetry.modules.activity_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.activity_metrics import record_service_activity


class TestRecordServiceActivity:
    def test_counts_activity_with_all_labels(self):
        record_service_activity(
            "indexing_service",
            "document_indexed",
            connector="gmail",
            status="ok",
            org="org-1",
            kb="kb-1",
            mimetype="application/pdf",
        )

        text = METRICS_BACKEND.serialize()
        line = next(
            line for line in text.splitlines() if 'activity="document_indexed"' in line
        )
        assert line.startswith("pipeshub_activity_total{")
        assert 'service="indexing_service"' in line
        assert 'connector="gmail"' in line
        assert 'status="ok"' in line
        assert 'org="org-1"' in line
        assert 'kb="kb-1"' in line
        assert 'mimetype="application/pdf"' in line
        assert line.endswith(" 1.0")

    def test_defaults_for_optional_labels(self):
        record_service_activity("query_service", "agent_run_defaults")

        text = METRICS_BACKEND.serialize()
        line = next(
            line for line in text.splitlines() if "agent_run_defaults" in line
        )
        assert 'connector="none"' in line
        assert 'status="ok"' in line
        assert 'org="unknown"' in line
        assert 'kb="none"' in line
        assert 'mimetype="none"' in line

    def test_repeat_calls_accumulate(self):
        record_service_activity("query_service", "repeat_case")
        record_service_activity("query_service", "repeat_case")

        text = METRICS_BACKEND.serialize()
        line = next(line for line in text.splitlines() if "repeat_case" in line)
        assert line.endswith(" 2.0")
