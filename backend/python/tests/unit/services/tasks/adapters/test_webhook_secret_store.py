"""Unit tests for `ConfigServiceWebhookSecretStore` -- Phase 8's reveal-once
webhook secret persistence over `ConfigurationService`. Uses a minimal fake
implementing only `get_config`/`set_config`/`delete_config` (the real
encryption-at-rest guarantee comes from `EncryptedKeyValueStore`, which is
`ConfigurationService`'s own concern, not this adapter's -- see this
module's docstring)."""
from __future__ import annotations

from app.services.tasks.adapters.config.webhook_secret_store import (
    ConfigServiceWebhookSecretStore,
)


class FakeConfigService:
    def __init__(self) -> None:
        self._store: dict[str, object] = {}
        self.set_calls: list[tuple[str, object]] = []
        self.delete_calls: list[str] = []

    async def set_config(self, key: str, value: object) -> bool:
        self.set_calls.append((key, value))
        self._store[key] = value
        return True

    async def get_config(self, key: str, default: object = None, use_cache: bool = False) -> object:
        return self._store.get(key, default)

    async def delete_config(self, key: str) -> bool:
        self.delete_calls.append(key)
        return self._store.pop(key, None) is not None


class TestConfigServiceWebhookSecretStore:
    async def test_store_then_get_roundtrips(self) -> None:
        config_service = FakeConfigService()
        store = ConfigServiceWebhookSecretStore(config_service)

        await store.store("wh-1", "s3cr3t")

        assert await store.get("wh-1") == "s3cr3t"

    async def test_get_missing_returns_none(self) -> None:
        store = ConfigServiceWebhookSecretStore(FakeConfigService())
        assert await store.get("no-such-webhook") is None

    async def test_store_writes_under_a_namespaced_path(self) -> None:
        config_service = FakeConfigService()
        store = ConfigServiceWebhookSecretStore(config_service)

        await store.store("wh-1", "s3cr3t")

        assert config_service.set_calls == [("/services/tasks/webhooks/wh-1", {"secret": "s3cr3t"})]

    async def test_store_overwrites_existing_secret(self) -> None:
        config_service = FakeConfigService()
        store = ConfigServiceWebhookSecretStore(config_service)

        await store.store("wh-1", "old-secret")
        await store.store("wh-1", "new-secret")

        assert await store.get("wh-1") == "new-secret"

    async def test_delete_removes_secret(self) -> None:
        config_service = FakeConfigService()
        store = ConfigServiceWebhookSecretStore(config_service)
        await store.store("wh-1", "s3cr3t")

        await store.delete("wh-1")

        assert await store.get("wh-1") is None

    async def test_delete_of_absent_secret_does_not_raise(self) -> None:
        store = ConfigServiceWebhookSecretStore(FakeConfigService())
        await store.delete("no-such-webhook")  # no raise

    async def test_get_ignores_malformed_non_dict_record(self) -> None:
        """A record without the expected `{"secret": ...}` shape (e.g. a
        stray key collision) must be treated as absent, not crash."""
        config_service = FakeConfigService()
        config_service._store["/services/tasks/webhooks/wh-1"] = "not-a-dict"
        store = ConfigServiceWebhookSecretStore(config_service)

        assert await store.get("wh-1") is None
