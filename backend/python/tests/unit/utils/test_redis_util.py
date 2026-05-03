"""Unit tests for app.utils.redis_util.build_redis_url()."""

from unittest.mock import patch

import pytest

from app.utils.redis_util import build_redis_url


class TestBuildRedisUrl:
    """Tests for build_redis_url()."""

    def test_basic_no_auth(self):
        config = {"host": "localhost", "port": 6379, "db": 0}
        result = build_redis_url(config)
        assert result == "redis://localhost:6379/0"

    def test_with_password_only(self):
        config = {"host": "redis.example.com", "port": 6380, "password": "secret", "db": 1}
        result = build_redis_url(config)
        assert result == "redis://:secret@redis.example.com:6380/1"

    def test_with_username_and_password(self):
        config = {
            "host": "redis.example.com",
            "port": 6379,
            "username": "admin",
            "password": "pass123",
            "db": 2,
        }
        result = build_redis_url(config)
        assert result == "redis://admin:pass123@redis.example.com:6379/2"

    def test_with_username_no_password(self):
        config = {
            "host": "redis.example.com",
            "port": 6379,
            "username": "admin",
            "db": 0,
        }
        result = build_redis_url(config)
        assert result == "redis://admin@redis.example.com:6379/0"

    def test_tls_enabled(self):
        config = {"host": "secure.redis.io", "port": 6380, "tls": True, "db": 0}
        result = build_redis_url(config)
        assert result.startswith("rediss://")
        assert result == "rediss://secure.redis.io:6380/0"

    def test_tls_disabled(self):
        config = {"host": "redis.local", "port": 6379, "tls": False, "db": 0}
        result = build_redis_url(config)
        assert result.startswith("redis://")
        assert not result.startswith("rediss://")

    def test_tls_with_auth(self):
        config = {
            "host": "secure.redis.io",
            "port": 6380,
            "tls": True,
            "username": "user",
            "password": "pw",
            "db": 3,
        }
        result = build_redis_url(config)
        assert result == "rediss://user:pw@secure.redis.io:6380/3"

    def test_defaults_when_keys_missing(self):
        """Missing keys should use defaults: host=localhost, port=6379, db=RedisConfig.REDIS_DB."""
        config = {}
        result = build_redis_url(config)
        assert "localhost" in result
        assert "6379" in result
        # db defaults to RedisConfig.REDIS_DB.value which is 0
        assert result.endswith("/0")

    def test_empty_password_ignored(self):
        """Empty or whitespace-only password should not appear in URL."""
        config = {"host": "h", "port": 6379, "password": "   ", "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_empty_username_ignored(self):
        """Empty or whitespace-only username should not appear in URL."""
        config = {"host": "h", "port": 6379, "username": "  ", "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_empty_username_with_valid_password(self):
        """Empty username with valid password yields `:pass@` form."""
        config = {"host": "h", "port": 6379, "username": "", "password": "pw", "db": 0}
        result = build_redis_url(config)
        assert result == "redis://:pw@h:6379/0"

    def test_none_password(self):
        """None password should not appear in URL."""
        config = {"host": "h", "port": 6379, "password": None, "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_none_username(self):
        """None username should not appear in URL."""
        config = {"host": "h", "port": 6379, "username": None, "db": 0}
        result = build_redis_url(config)
        assert result == "redis://h:6379/0"

    def test_custom_db_number(self):
        config = {"host": "h", "port": 6379, "db": 15}
        result = build_redis_url(config)
        assert result.endswith("/15")

    def test_tls_default_is_false(self):
        """When tls key is missing, scheme should be redis:// not rediss://."""
        config = {"host": "h", "port": 6379, "db": 0}
        result = build_redis_url(config)
        assert result.startswith("redis://")
        assert not result.startswith("rediss://")
