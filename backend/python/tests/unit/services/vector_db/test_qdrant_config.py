"""Unit tests for app.services.vector_db.qdrant.config.QdrantConfig."""

import pytest

from app.services.vector_db.qdrant.config import QdrantConfig


class TestQdrantConfigInit:
    """Tests for QdrantConfig dataclass construction."""

    def test_basic_construction(self):
        config = QdrantConfig(
            host="localhost",
            port=6333,
            api_key="test_key",
            prefer_grpc=True,
            https=False,
            timeout=180,
        )
        assert config.host == "localhost"
        assert config.port == 6333
        assert config.api_key == "test_key"
        assert config.prefer_grpc is True
        assert config.https is False
        assert config.timeout == 180

    def test_construction_with_https(self):
        config = QdrantConfig(
            host="qdrant.example.com",
            port=443,
            api_key="secure_key",
            prefer_grpc=False,
            https=True,
            timeout=300,
        )
        assert config.host == "qdrant.example.com"
        assert config.port == 443
        assert config.https is True
        assert config.prefer_grpc is False
        assert config.timeout == 300


class TestQdrantConfigProperty:
    """Tests for QdrantConfig.qdrant_config property."""

    def test_qdrant_config_returns_dict(self):
        config = QdrantConfig(
            host="localhost",
            port=6333,
            api_key="test_key",
            prefer_grpc=True,
            https=False,
            timeout=180,
        )
        result = config.qdrant_config
        assert isinstance(result, dict)
        assert result == {
            "host": "localhost",
            "port": 6333,
            "api_key": "test_key",
            "prefer_grpc": True,
            "https": False,
            "timeout": 180,
        }

    def test_qdrant_config_all_fields_present(self):
        config = QdrantConfig(
            host="h", port=1, api_key="k", prefer_grpc=False, https=True, timeout=10
        )
        result = config.qdrant_config
        assert set(result.keys()) == {"host", "port", "api_key", "prefer_grpc", "https", "timeout"}


class TestQdrantConfigFromDict:
    """Tests for QdrantConfig.from_dict classmethod."""

    def test_from_dict_full(self):
        data = {
            "host": "remote-host",
            "port": 6334,
            "api_key": "my_api_key",
            "prefer_grpc": False,
            "https": True,
            "timeout": 60,
        }
        config = QdrantConfig.from_dict(data)
        assert config.host == "remote-host"
        assert config.port == 6334
        assert config.api_key == "my_api_key"
        assert config.prefer_grpc is False
        assert config.https is True
        assert config.timeout == 60

    def test_from_dict_empty_uses_defaults(self):
        config = QdrantConfig.from_dict({})
        assert config.host == ""
        assert config.port == 0
        assert config.api_key == ""
        assert config.prefer_grpc is True
        assert config.https is False
        assert config.timeout == 180

    def test_from_dict_partial(self):
        data = {"host": "partial-host", "port": 9999}
        config = QdrantConfig.from_dict(data)
        assert config.host == "partial-host"
        assert config.port == 9999
        # Defaults for missing keys
        assert config.api_key == ""
        assert config.prefer_grpc is True
        assert config.https is False
        assert config.timeout == 180

    def test_from_dict_extra_keys_ignored(self):
        data = {
            "host": "h",
            "port": 1,
            "api_key": "k",
            "prefer_grpc": True,
            "https": False,
            "timeout": 100,
            "extra_field": "should_be_ignored",
        }
        config = QdrantConfig.from_dict(data)
        assert config.host == "h"
        assert config.timeout == 100
        assert not hasattr(config, "extra_field") or "extra_field" not in config.__dict__

    def test_from_dict_roundtrip(self):
        """Creating from dict and converting back via qdrant_config should roundtrip."""
        original = {
            "host": "rt-host",
            "port": 7777,
            "api_key": "rt-key",
            "prefer_grpc": False,
            "https": True,
            "timeout": 42,
        }
        config = QdrantConfig.from_dict(original)
        result = config.qdrant_config
        assert result == original
