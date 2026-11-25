"""A connector's cleanup is recorded under its org and id: no write or delete for one org reaches another's."""

import pytest

from app.connectors.services.connector_intents import (
    ConnectorIntentStore,
    held_by_another_org,
)

from .test_late_write_sweep import Config


class RefusedError(Exception):
    pass


STORE = ConnectorIntentStore("/services/cleanup/pending/", RefusedError, "cleanup")
A, B = "/services/cleanup/pending/org-a/c1", "/services/cleanup/pending/org-b/c1"
EARLIER = "/services/cleanup/pending/c1"


async def test_two_orgs_intents_for_one_connector_id_live_apart_whatever_the_order() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1)
    await STORE.record(config, org_id="org-b", connector_id="c1", now_ms=2)
    theirs = config.kv[B]
    assert {i["orgId"] for i in await STORE.list(config)} == {"org-a", "org-b"}

    assert await STORE.reschedule(config, config.kv[A], next_attempt_at=9)  # type: ignore[arg-type]
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=3)
    assert config.kv[B] == theirs
    assert await STORE.clear(config, "org-a", "c1")
    assert config.kv == {B: theirs}


async def test_a_write_or_delete_for_one_org_names_no_key_another_org_uses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Another org records in every gap between a read and the write after it: nothing of it is ever touched."""
    config = Config()
    theirs = {"orgId": "org-b", "connectorId": "c1", "requestedAt": 7}
    read = Config.get_config

    async def racing(self: Config, key: str, *a: object, **kw: object) -> object:
        value = await read(self, key, *a, **kw)
        self.kv[B] = theirs
        return value

    monkeypatch.setattr(Config, "get_config", racing)
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1)
    await STORE.reschedule(config, {"orgId": "org-a", "connectorId": "c1"}, next_attempt_at=9)
    await STORE.clear(config, "org-a", "c1")
    assert config.kv == {B: theirs}


async def test_an_earlier_versions_intent_is_listed_read_and_cleared_by_its_own_org_only() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "recordGroupIds": ["g1"]}
    assert await STORE.read(config, "org-b", "c1") is None
    assert await STORE.clear(config, "org-b", "c1") and EARLIER in config.kv
    [intent] = await STORE.list(config)

    await STORE.reschedule(config, intent, next_attempt_at=9)
    [merged] = await STORE.list(config)
    assert (merged["attempts"], merged["recordGroupIds"]) == (1, ["g1"])
    assert await STORE.clear(config, "org-a", "c1") and config.kv == {}


async def test_the_groups_of_both_versions_intents_are_kept() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "recordGroupIds": ["g1"]}
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=2, recordGroupIds=["g2"])
    [merged] = await STORE.list(config)
    assert (merged["requestedAt"], merged["recordGroupIds"]) == (2, ["g2", "g1"])
    assert (await STORE.read(config, "org-a", "c1") or {})["recordGroupIds"] == ["g2", "g1"]


async def test_groups_added_since_the_intent_was_read_survive_a_reschedule() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1, recordGroupIds=["g1", "g2"])
    assert await STORE.reschedule(
        config, {"orgId": "org-a", "connectorId": "c1", "recordGroupIds": ["g1"]}, next_attempt_at=9,
    )
    assert config.kv[A]["recordGroupIds"] == ["g1", "g2"]


async def test_an_intent_that_cannot_be_read_is_not_rescheduled_or_taken_for_cleared() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1)
    before = dict(config.kv)
    config.read_error = ConnectionError("down")
    with pytest.raises(ConnectionError):
        await STORE.reschedule(config, {"orgId": "org-a", "connectorId": "c1"}, next_attempt_at=9)
    assert not await STORE.clear(config, "org-a", "c1")
    assert config.kv == before


async def test_an_entry_whose_key_names_another_org_is_not_listed() -> None:
    config = Config()
    config.kv[B] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    assert await STORE.list(config) == []


@pytest.mark.parametrize(("app", "other"), [
    ({"orgId": "org-b"}, True), ({"orgId": "org-a"}, False), ({"orgId": None}, False), ({}, False),
])
def test_an_app_with_no_org_is_not_another_orgs(app: dict, other: bool) -> None:
    assert held_by_another_org(app, "org-a") is other


async def test_a_malformed_group_list_does_not_stop_the_listing() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "recordGroupIds": 3}
    await STORE.record(config, org_id="org-b", connector_id="c2", now_ms=1)
    assert {i["orgId"] for i in await STORE.list(config)} == {"org-a", "org-b"}
