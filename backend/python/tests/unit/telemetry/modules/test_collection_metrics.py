"""Unit tests for app.telemetry.modules.collection_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.collection_metrics import set_metric_collection_enabled


class TestSetMetricCollectionEnabled:
    def test_enabled_publishes_one(self):
        set_metric_collection_enabled("svc_enabled", True)

        assert (
            'pipeshub_metric_collection_enabled{service="svc_enabled"} 1.0'
            in METRICS_BACKEND.serialize()
        )

    def test_disabled_publishes_zero(self):
        set_metric_collection_enabled("svc_disabled", False)

        assert (
            'pipeshub_metric_collection_enabled{service="svc_disabled"} 0.0'
            in METRICS_BACKEND.serialize()
        )

    def test_consent_flip_overwrites_value(self):
        set_metric_collection_enabled("svc_flip", True)
        set_metric_collection_enabled("svc_flip", False)

        text = METRICS_BACKEND.serialize()
        assert 'pipeshub_metric_collection_enabled{service="svc_flip"} 0.0' in text
        assert 'pipeshub_metric_collection_enabled{service="svc_flip"} 1.0' not in text
