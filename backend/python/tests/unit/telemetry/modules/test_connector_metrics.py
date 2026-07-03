"""Unit tests for app.telemetry.modules.connector_metrics."""

from app.telemetry.backend import METRICS_BACKEND
from app.telemetry.modules.connector_metrics import set_connector_active


class TestSetConnectorActive:
    def test_tuple_keys_carry_connector_and_domain(self):
        set_connector_active({("gmail", "acme.io"): 3})

        assert (
            'pipeshub_connector_active{connector="gmail",domain="acme.io"} 3.0'
            in METRICS_BACKEND.serialize()
        )

    def test_plain_string_key_defaults_domain_to_unknown(self):
        set_connector_active({"slack": 2})

        assert (
            'pipeshub_connector_active{connector="slack",domain="unknown"} 2.0'
            in METRICS_BACKEND.serialize()
        )

    def test_falsy_connector_and_domain_become_unknown(self):
        set_connector_active({("", None): 1})

        assert (
            'pipeshub_connector_active{connector="unknown",domain="unknown"} 1.0'
            in METRICS_BACKEND.serialize()
        )

    def test_refresh_replaces_stale_series(self):
        set_connector_active({("stale_connector", "x.io"): 5})
        set_connector_active({("fresh_connector", "y.io"): 1})

        text = METRICS_BACKEND.serialize()
        assert 'connector="stale_connector"' not in text
        assert (
            'pipeshub_connector_active{connector="fresh_connector",domain="y.io"} 1.0'
            in text
        )
