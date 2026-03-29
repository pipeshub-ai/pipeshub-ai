"""
Unit tests for ArangoConfig dataclass (app/services/graph_db/arango/config.py).

Tests cover:
- Direct construction with all fields
- from_dict: complete dict, partial dict (defaults), empty dict
- to_dict: round-trip serialization
- arango_config property: returns same format as to_dict
- Equality / dataclass behaviour
"""

import pytest

from app.services.graph_db.arango.config import ArangoConfig


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestArangoConfigConstruction:
    def test_direct_construction(self):
        config = ArangoConfig(
            url="http://localhost:8529",
            db="test_db",
            username="root",
            password="secret",
        )
        assert config.url == "http://localhost:8529"
        assert config.db == "test_db"
        assert config.username == "root"
        assert config.password == "secret"

    def test_fields_are_mutable(self):
        config = ArangoConfig(url="a", db="b", username="c", password="d")
        config.url = "http://new:8529"
        assert config.url == "http://new:8529"


# ---------------------------------------------------------------------------
# from_dict
# ---------------------------------------------------------------------------


class TestFromDict:
    def test_from_dict_complete(self):
        data = {
            "url": "http://host:8529",
            "db": "mydb",
            "username": "user",
            "password": "pass",
        }
        config = ArangoConfig.from_dict(data)
        assert config.url == "http://host:8529"
        assert config.db == "mydb"
        assert config.username == "user"
        assert config.password == "pass"

    def test_from_dict_partial_defaults_to_empty_string(self):
        data = {"url": "http://host:8529"}
        config = ArangoConfig.from_dict(data)
        assert config.url == "http://host:8529"
        assert config.db == ""
        assert config.username == ""
        assert config.password == ""

    def test_from_dict_empty(self):
        config = ArangoConfig.from_dict({})
        assert config.url == ""
        assert config.db == ""
        assert config.username == ""
        assert config.password == ""

    def test_from_dict_extra_fields_ignored(self):
        data = {
            "url": "http://host:8529",
            "db": "mydb",
            "username": "user",
            "password": "pass",
            "extra_field": "ignored",
        }
        config = ArangoConfig.from_dict(data)
        assert config.url == "http://host:8529"
        assert not hasattr(config, "extra_field")


# ---------------------------------------------------------------------------
# to_dict
# ---------------------------------------------------------------------------


class TestToDict:
    def test_to_dict(self):
        config = ArangoConfig(
            url="http://host:8529",
            db="mydb",
            username="user",
            password="pass",
        )
        result = config.to_dict()
        assert result == {
            "url": "http://host:8529",
            "db": "mydb",
            "username": "user",
            "password": "pass",
        }

    def test_to_dict_returns_new_dict(self):
        config = ArangoConfig(url="a", db="b", username="c", password="d")
        d1 = config.to_dict()
        d2 = config.to_dict()
        assert d1 == d2
        assert d1 is not d2  # Should be a new dict each time

    def test_round_trip(self):
        original = {"url": "http://x:8529", "db": "db", "username": "u", "password": "p"}
        config = ArangoConfig.from_dict(original)
        assert config.to_dict() == original


# ---------------------------------------------------------------------------
# arango_config property
# ---------------------------------------------------------------------------


class TestArangoConfigProperty:
    def test_arango_config_property(self):
        config = ArangoConfig(
            url="http://host:8529",
            db="mydb",
            username="user",
            password="pass",
        )
        prop = config.arango_config
        assert prop == {
            "url": "http://host:8529",
            "db": "mydb",
            "username": "user",
            "password": "pass",
        }

    def test_arango_config_property_matches_to_dict(self):
        config = ArangoConfig(url="a", db="b", username="c", password="d")
        assert config.arango_config == config.to_dict()


# ---------------------------------------------------------------------------
# Dataclass equality
# ---------------------------------------------------------------------------


class TestDataclassBehaviour:
    def test_equality(self):
        c1 = ArangoConfig(url="a", db="b", username="c", password="d")
        c2 = ArangoConfig(url="a", db="b", username="c", password="d")
        assert c1 == c2

    def test_inequality(self):
        c1 = ArangoConfig(url="a", db="b", username="c", password="d")
        c2 = ArangoConfig(url="a", db="b", username="c", password="DIFFERENT")
        assert c1 != c2

    def test_repr(self):
        config = ArangoConfig(url="a", db="b", username="c", password="d")
        r = repr(config)
        assert "ArangoConfig" in r
        assert "url='a'" in r
