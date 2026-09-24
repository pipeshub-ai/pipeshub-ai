"""Unit tests for app.services.redis.config (RedisConnectionConfig, ClientOptions)."""

from unittest.mock import patch

import pytest

from app.services.redis.config import ClientOptions, RedisConnectionConfig


class TestClientOptionsDefaults:
    def test_defaults(self):
        opts = ClientOptions()
        assert opts.decode_responses is True
        assert opts.max_connections == 10
        assert opts.blocking is False


class TestRedisConnectionConfigFromEnv:
    def test_defaults_when_env_empty(self):
        with patch.dict("os.environ", {}, clear=True):
            cfg = RedisConnectionConfig.from_env()

        assert cfg.host == "localhost"
        assert cfg.port == 6379
        assert cfg.db == 0
        assert cfg.tls is False
        assert cfg.cluster_endpoints == []
        assert cfg.key_namespace == ""

    def test_reads_all_env_vars(self):
        env = {
            "REDIS_HOST": "redis.example.com",
            "REDIS_PORT": "6380",
            "REDIS_USERNAME": "svc",
            "REDIS_PASSWORD": "s3cret",
            "REDIS_TLS_ENABLED": "true",
            "REDIS_TLS_REJECT_UNAUTHORIZED": "false",
            "REDIS_TLS_CA_PATH": "/etc/certs/ca.pem",
            "REDIS_DB": "3",
            "REDIS_KEY_NAMESPACE": "tenant-a",
            "REDIS_TIMEOUT": "5000",
            "REDIS_CLUSTER_ENDPOINTS": "n1:7000, n2:7001,n3:7002",
            "REDIS_CLUSTER_SCALE_READS": "all",
        }
        with patch.dict("os.environ", env, clear=True):
            cfg = RedisConnectionConfig.from_env()

        assert cfg.host == "redis.example.com"
        assert cfg.port == 6380
        assert cfg.username == "svc"
        assert cfg.password == "s3cret"
        assert cfg.tls is True
        assert cfg.tls_reject_unauthorized is False
        assert cfg.tls_ca_path == "/etc/certs/ca.pem"
        assert cfg.db == 3
        assert cfg.key_namespace == "tenant-a"
        assert cfg.connect_timeout_seconds == 5.0
        assert cfg.cluster_endpoints == ["n1:7000", "n2:7001", "n3:7002"]
        assert cfg.scale_reads == "all"

    def test_empty_password_becomes_none(self):
        with patch.dict("os.environ", {"REDIS_PASSWORD": ""}, clear=True):
            cfg = RedisConnectionConfig.from_env()
        assert cfg.password is None


class TestRedisConnectionConfigFromHostPort:
    def test_overrides_host_port_password_db_username(self):
        with patch.dict(
            "os.environ", {"REDIS_TLS_ENABLED": "true"}, clear=True
        ):
            cfg = RedisConnectionConfig.from_host_port(
                host="legacy-host", port=6390, password="pw", db=2, username="u"
            )

        assert cfg.host == "legacy-host"
        assert cfg.port == 6390
        assert cfg.password == "pw"
        assert cfg.db == 2
        assert cfg.username == "u"
        # Process-wide settings (TLS, namespace, cluster endpoints) still come
        # from the environment rather than being reset by the legacy shape.
        assert cfg.tls is True


class TestEnvFlagSpellings:
    """`REDIS_TLS_*` must accept what operators actually write.

    Matching only the literal "true" meant `REDIS_TLS_ENABLED=1` produced a
    plaintext connection still carrying the Redis password, and -- because it
    defaults on -- `REDIS_TLS_REJECT_UNAUTHORIZED=yes` silently *disabled*
    certificate verification. Both fail open with nothing logged.
    """

    @pytest.mark.parametrize("value", ["1", "yes", "on", "true", "TRUE", " true "])
    def test_truthy_spellings_enable_tls(self, monkeypatch, value: str) -> None:
        monkeypatch.setenv("REDIS_TLS_ENABLED", value)
        assert RedisConnectionConfig.from_env().tls is True

    @pytest.mark.parametrize("value", ["0", "no", "off", "false", " FALSE "])
    def test_falsy_spellings_disable_verification(
        self, monkeypatch, value: str
    ) -> None:
        monkeypatch.setenv("REDIS_TLS_REJECT_UNAUTHORIZED", value)
        assert RedisConnectionConfig.from_env().tls_reject_unauthorized is False

    @pytest.mark.parametrize(
        "name", ["REDIS_TLS_ENABLED", "REDIS_TLS_REJECT_UNAUTHORIZED"]
    )
    def test_an_unparseable_value_is_refused_not_guessed(
        self, monkeypatch, name: str
    ) -> None:
        """A typo has no safe reading: `REDIS_TLS_ENABLED=ture` would mean
        plaintext and `REDIS_TLS_REJECT_UNAUTHORIZED=yse` would mean
        unverified, both silently. Refusing to start beats guessing."""
        monkeypatch.setenv(name, "ture")
        with pytest.raises(ValueError, match="not a valid boolean"):
            RedisConnectionConfig.from_env()

    @pytest.mark.parametrize("value", ["", "   "])
    def test_a_blank_value_still_falls_back(self, monkeypatch, value: str) -> None:
        """Blank is "unset" (compose writes `VAR=${VAR:-}`), not a typo."""
        monkeypatch.setenv("REDIS_TLS_ENABLED", value)
        monkeypatch.setenv("REDIS_TLS_REJECT_UNAUTHORIZED", value)
        config = RedisConnectionConfig.from_env()
        assert config.tls is False
        assert config.tls_reject_unauthorized is True
