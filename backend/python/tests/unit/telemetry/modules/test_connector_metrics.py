"""Unit tests for app.telemetry.modules.connector_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.connector_metrics import set_connector_active


class TestSetConnectorActive:
    def test_one_series_per_connector_with_no_domain_label(self):
        set_connector_active({"slack": 2})

        text = METRICS_BACKEND.serialize()
        assert 'pipeshub_connector_active{connector="slack"} 2.0' in text
        assert "domain=" not in text

    def test_empty_connector_becomes_unknown(self):
        set_connector_active({"": 1})

        assert 'pipeshub_connector_active{connector="unknown"} 1.0' in METRICS_BACKEND.serialize()

    def test_refresh_replaces_stale_series(self):
        set_connector_active({"stale_connector": 5})
        set_connector_active({"fresh_connector": 1})

        text = METRICS_BACKEND.serialize()
        assert 'connector="stale_connector"' not in text
        assert 'pipeshub_connector_active{connector="fresh_connector"} 1.0' in text
