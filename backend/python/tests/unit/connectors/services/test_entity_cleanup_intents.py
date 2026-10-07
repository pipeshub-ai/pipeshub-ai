"""A connector's entity cleanup is recorded before its graph rows go, so a
lost deleteConnectorEntities event is still reconciled (KG-45 review)."""
from __future__ import annotations

import pytest

from app.connectors.services.entity_cleanup_intents import (
    PENDING_DIRECTORY,
    EntityCleanupIntentError,
    clear_pending_entity_cleanup,
    list_pending_entity_cleanups,
    record_pending_entity_cleanup,
)


class FakeConfig:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}
        self.fail_set = self.fail_delete = self.fail_list = False

    async def set_config(self, key: str, value: object) -> bool:
        if self.fail_set:
            return False
        self.kv[key] = value
        return True

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
        assert use_cache is False  # another process wrote it
        return self.kv.get(key, default)

    async def delete_config(self, key: str) -> bool:
        if self.fail_delete:
            return False
        self.kv.pop(key, None)
        return True

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        if self.fail_list:
            raise RuntimeError("kv down")
        return [k for k in self.kv if k.startswith(directory)]


async def test_an_intent_is_recorded_listed_and_cleared() -> None:
    config = FakeConfig()
    await record_pending_entity_cleanup(config, org_id="o", connector_id="c", connector_name="DRIVE", now_ms=5)
    assert list(config.kv) == [f"{PENDING_DIRECTORY}c"]
    assert await list_pending_entity_cleanups(config) == [
        {"orgId": "o", "connectorId": "c", "connectorName": "DRIVE", "requestedAt": 5},
    ]
    assert await clear_pending_entity_cleanup(config, "c") is True
    assert await list_pending_entity_cleanups(config) == []


async def test_an_intent_that_cannot_be_recorded_raises() -> None:
    """The caller must not delete graph rows it then cannot reconcile."""
    config = FakeConfig()
    config.fail_set = True
    with pytest.raises(EntityCleanupIntentError):
        await record_pending_entity_cleanup(config, org_id="o", connector_id="c", connector_name=None)


async def test_a_malformed_entry_is_skipped() -> None:
    config = FakeConfig()
    config.kv[f"{PENDING_DIRECTORY}bad"] = {"connectorId": "bad"}
    config.kv[f"{PENDING_DIRECTORY}also-bad"] = "not a dict"
    assert await list_pending_entity_cleanups(config) == []


async def test_a_failed_clear_is_reported_not_raised() -> None:
    config = FakeConfig()
    await record_pending_entity_cleanup(config, org_id="o", connector_id="c", connector_name=None)
    config.fail_delete = True
    assert await clear_pending_entity_cleanup(config, "c") is False


async def test_clearing_an_absent_intent_writes_nothing() -> None:
    """A delete made before intents existed, or one the rebuild already
    settled: the store would log a failed delete for a missing key."""
    config = FakeConfig()
    config.fail_delete = True
    assert await clear_pending_entity_cleanup(config, "never-recorded") is True
