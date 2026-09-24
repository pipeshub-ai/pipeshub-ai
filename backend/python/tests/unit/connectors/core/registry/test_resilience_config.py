"""Tests for resilience config plumbing: builder -> decorator -> class metadata."""

from unittest.mock import MagicMock

import pytest

from app.connectors.core.registry.connector_builder import ConnectorBuilder
from app.connectors.core.registry.connector_registry import Connector, ConnectorRegistry


class TestBuilder:
    def test_absent_by_default(self):
        assert ConnectorBuilder("Test").resilience_config is None

    def test_records_all_knobs(self):
        builder = ConnectorBuilder("Test").with_resilience_config(
            rate_limit=3, max_retries=2, base_delay=0.5, max_delay=30.0
        )
        assert builder.resilience_config == {
            "enabled": True,
            "rate_limit": 3,
            "max_retries": 2,
            "base_delay": 0.5,
            "max_delay": 30.0,
        }

    def test_rate_limit_is_required(self):
        """No default: a limit that is wrong for a given API is worse than none."""
        with pytest.raises(TypeError):
            ConnectorBuilder("Test").with_resilience_config()

    def test_is_chainable(self):
        builder = ConnectorBuilder("Test")
        assert builder.with_resilience_config(rate_limit=3) is builder


class TestDecorator:
    def test_stores_config_in_class_metadata(self):
        config = {"enabled": True, "rate_limit": 3, "max_retries": 3}

        @Connector(name="T", app_group="G", supported_auth_types=["API_TOKEN"], resilience_config=config)
        class _Connector:
            pass

        assert _Connector._connector_metadata["resilienceConfig"] == config

    def test_defaults_to_empty_dict(self):
        @Connector(name="T", app_group="G", supported_auth_types=["API_TOKEN"])
        class _Connector:
            pass

        assert _Connector._connector_metadata["resilienceConfig"] == {}

    def test_not_exposed_to_api_or_database(self):
        """resilienceConfig is an internal runtime knob, not connector metadata
        the frontend or apps collection should carry."""
        registry = ConnectorRegistry.__new__(ConnectorRegistry)
        registry.logger = MagicMock()
        metadata = {
            "name": "T",
            "appGroup": "G",
            "supportedAuthTypes": ["API_TOKEN"],
            "config": {},
            "resilienceConfig": {"rate_limit": 3},
        }

        info = registry._build_connector_info("T", metadata)

        assert "resilienceConfig" not in info
        assert "resilienceConfig" not in info.get("config", {})
