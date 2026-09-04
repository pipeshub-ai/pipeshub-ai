"""Unit tests for app.services.redis.connection_provider_factory.

Covers the extension seam a separate EE repo relies on to add AWS MemoryDB
support (register + REDIS_MODE + REDIS_PROVIDER_MODULE) with zero changes to
this module, plus the process-level singleton cache (R11).
"""

from unittest.mock import MagicMock, patch

import pytest

from app.services.redis.config import RedisConnectionConfig
from app.services.redis.connection_provider import IRedisConnectionProvider
from app.services.redis.connection_provider_factory import (
    RedisConnectionProviderFactory,
    get_redis_provider,
    reset_redis_provider_registry,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_redis_provider_registry()
    yield
    reset_redis_provider_registry()


class _FakeProvider(IRedisConnectionProvider):
    def __init__(self, config: RedisConnectionConfig) -> None:
        self.config = config

    def get_client(self): ...
    def create_client(self, options=None): ...
    def create_pubsub_client(self): ...
    async def scan_keys(self, pattern, count=100):
        return
        yield  # pragma: no cover - never reached; keeps this an async generator

    async def load_script(self, body):
        return "sha"

    def key_slot(self, key):
        return 0

    def connection_url(self):
        return "redis://fake"

    async def ping(self):
        return True

    async def close(self):
        pass

    @property
    def is_cluster(self):
        return False

    @property
    def mode(self):
        return "fake"

    @property
    def key_namespace(self):
        return self.config.key_namespace


class TestCreate:
    def test_defaults_to_standalone_mode(self):
        with patch.dict("os.environ", {}, clear=True):
            provider = RedisConnectionProviderFactory.create(RedisConnectionConfig())
        assert provider.mode == "standalone"

    def test_unknown_mode_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown REDIS_MODE"):
            RedisConnectionProviderFactory.create(RedisConnectionConfig(), mode="memorydb")

    def test_registered_mode_is_used(self):
        RedisConnectionProviderFactory.register("fake", _FakeProvider)
        try:
            provider = RedisConnectionProviderFactory.create(
                RedisConnectionConfig(), mode="fake"
            )
            assert isinstance(provider, _FakeProvider)
        finally:
            RedisConnectionProviderFactory._registry.pop("fake", None)

    def test_rejects_db_outside_standalone_mode(self):
        RedisConnectionProviderFactory.register("fake", _FakeProvider)
        try:
            with pytest.raises(ValueError, match="REDIS_DB is not supported"):
                RedisConnectionProviderFactory.create(
                    RedisConnectionConfig(db=1), mode="fake"
                )
        finally:
            RedisConnectionProviderFactory._registry.pop("fake", None)

    def test_standalone_mode_allows_db(self):
        provider = RedisConnectionProviderFactory.create(
            RedisConnectionConfig(db=1), mode="standalone"
        )
        assert provider.mode == "standalone"

    def test_registered_modes_includes_oss_defaults(self):
        modes = RedisConnectionProviderFactory.registered_modes()
        assert "standalone" in modes
        assert "cluster" in modes


class TestDiscoverViaProviderModule:
    def test_imports_configured_module_before_raising(self):
        with patch.dict("os.environ", {"REDIS_MODE": "memorydb", "REDIS_PROVIDER_MODULE": "does.not.exist"}, clear=True):
            with pytest.raises(ValueError, match="Unknown REDIS_MODE"):
                RedisConnectionProviderFactory.create(RedisConnectionConfig())

    def test_module_registering_a_mode_makes_it_available(self):
        module_name = "tests.unit.services.redis._fake_provider_module"
        with patch.dict(
            "os.environ",
            {"REDIS_MODE": "fake-registered", "REDIS_PROVIDER_MODULE": module_name},
            clear=True,
        ):
            fake_module = MagicMock()

            def _register_on_import(name):
                RedisConnectionProviderFactory.register("fake-registered", _FakeProvider)
                return fake_module

            with patch("importlib.import_module", side_effect=_register_on_import):
                try:
                    provider = RedisConnectionProviderFactory.create(RedisConnectionConfig())
                    assert isinstance(provider, _FakeProvider)
                finally:
                    RedisConnectionProviderFactory._registry.pop("fake-registered", None)


class TestGetRedisProviderSingleton:
    def test_same_config_returns_cached_instance(self):
        config = RedisConnectionConfig(host="h", port=1, db=0)
        p1 = get_redis_provider(config, mode="standalone")
        p2 = get_redis_provider(config, mode="standalone")
        assert p1 is p2

    def test_different_host_returns_different_instance(self):
        p1 = get_redis_provider(RedisConnectionConfig(host="h1"), mode="standalone")
        p2 = get_redis_provider(RedisConnectionConfig(host="h2"), mode="standalone")
        assert p1 is not p2

    def test_different_mode_returns_different_instance(self):
        RedisConnectionProviderFactory.register("fake", _FakeProvider)
        try:
            config = RedisConnectionConfig(host="h")
            p1 = get_redis_provider(config, mode="standalone")
            p2 = get_redis_provider(config, mode="fake")
            assert p1 is not p2
        finally:
            RedisConnectionProviderFactory._registry.pop("fake", None)

    def test_reset_registry_forces_new_instance(self):
        config = RedisConnectionConfig(host="h")
        p1 = get_redis_provider(config, mode="standalone")
        reset_redis_provider_registry()
        p2 = get_redis_provider(config, mode="standalone")
        assert p1 is not p2

    def test_different_password_returns_different_instance(self):
        p1 = get_redis_provider(
            RedisConnectionConfig(host="h", password="pw1"), mode="standalone"
        )
        p2 = get_redis_provider(
            RedisConnectionConfig(host="h", password="pw2"), mode="standalone"
        )
        assert p1 is not p2

    def test_different_username_returns_different_instance(self):
        p1 = get_redis_provider(
            RedisConnectionConfig(host="h", username="u1"), mode="standalone"
        )
        p2 = get_redis_provider(
            RedisConnectionConfig(host="h", username="u2"), mode="standalone"
        )
        assert p1 is not p2

    def test_different_tls_returns_different_instance(self):
        p1 = get_redis_provider(
            RedisConnectionConfig(host="h", tls=False), mode="standalone"
        )
        p2 = get_redis_provider(
            RedisConnectionConfig(host="h", tls=True), mode="standalone"
        )
        assert p1 is not p2

    def test_different_scale_reads_returns_different_instance(self):
        p1 = get_redis_provider(
            RedisConnectionConfig(host="h", scale_reads="master"), mode="standalone"
        )
        p2 = get_redis_provider(
            RedisConnectionConfig(host="h", scale_reads="all"), mode="standalone"
        )
        assert p1 is not p2


class TestCredentialsOverUnverifiedTls:
    """TLS with verification off is encrypted but *unauthenticated* (CWE-295).

    A MITM can present any certificate, terminate the session, and harvest the
    password. The guard lives in the factory rather than in each provider so an
    EE-registered provider is covered by the same rule.
    """

    @staticmethod
    def _config(**overrides) -> RedisConnectionConfig:
        base = {"host": "localhost", "port": 6379, "tls": True}
        base.update(overrides)
        return RedisConnectionConfig(**base)

    @pytest.mark.parametrize(
        "credential", [{"password": "secret"}, {"username": "acl-user"}]
    )
    def test_credentials_are_refused_when_verification_is_disabled(
        self, credential: dict
    ) -> None:
        config = self._config(tls_reject_unauthorized=False, **credential)
        with pytest.raises(ValueError, match="encrypted but not authenticated"):
            RedisConnectionProviderFactory.create(config, mode="standalone")

    def test_credentials_are_allowed_when_verification_is_on(self) -> None:
        config = self._config(password="secret")
        assert RedisConnectionProviderFactory.create(config, mode="standalone")

    def test_unverified_tls_is_allowed_with_nothing_to_leak(self) -> None:
        config = self._config(tls_reject_unauthorized=False)
        assert RedisConnectionProviderFactory.create(config, mode="standalone")

    def test_the_default_install_is_untouched(self) -> None:
        """Deliberate, and repeatedly re-raised in review -- reasoning here
        rather than in a PR thread.

        Requiring TLS whenever a password is set would break every default
        install: Compose and Helm both ship ``REDIS_PASSWORD`` with TLS off.
        That posture is not Redis-specific -- in the same compose file MongoDB
        (``mongodb://user:pass@mongodb:27017``), Neo4j (``bolt://``, not
        ``bolt+s://``) and Qdrant (plain gRPC + API key) all send credentials
        the same way, and none of the four publishes a port to the host: they
        are reachable only on the Docker bridge / cluster network. Singling
        Redis out would be inconsistent without improving anything, and
        applying it to all four would require every user to provision a PKI
        before ``docker compose up`` -- a product decision, not a review fix.

        What IS enforced, because it has no legitimate reading: TLS on +
        verification off + credentials is rejected.
        """
        config = self._config(tls=False, password="secret")
        assert RedisConnectionProviderFactory.create(config, mode="standalone")
