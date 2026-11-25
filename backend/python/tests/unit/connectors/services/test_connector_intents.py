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


async def test_an_earlier_versions_intent_is_listed_read_and_settled_by_its_own_org_only() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "recordGroupIds": ["g1"]}
    earlier = dict(config.kv)
    assert await STORE.read(config, "org-b", "c1") is None
    assert await STORE.clear(config, "org-b", "c1") and config.kv == earlier
    [intent] = await STORE.list(config)

    await STORE.reschedule(config, intent, next_attempt_at=9)
    [merged] = await STORE.list(config)
    assert (merged["attempts"], merged["recordGroupIds"]) == (1, ["g1"])
    assert await STORE.clear(config, "org-a", "c1")
    assert config.kv[EARLIER] == earlier[EARLIER]  # left where it is, and no longer pending
    assert await STORE.list(config) == [] and await STORE.read(config, "org-a", "c1") is None
    assert await STORE.clear(config, "org-a", "c1")


@pytest.mark.parametrize("gap", ["before the write", "after the write"])
async def test_an_old_replica_swapping_in_another_orgs_intent_loses_nothing(
    monkeypatch: pytest.MonkeyPatch, gap: str,
) -> None:
    """A replica of the earlier version removes org A's entry and records org B's, around every step of org A's clear."""
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    theirs = {"orgId": "org-b", "connectorId": "c1", "requestedAt": 2}
    read, write = Config.get_config, Config.set_config
    swapped = False

    async def swap_after_read(self: Config, key: str, *a: object, **kw: object) -> object:
        value = await read(self, key, *a, **kw)
        nonlocal swapped
        if not swapped:
            swapped, self.kv[EARLIER] = True, theirs
        return value

    async def swap_after_write(self: Config, key: str, value: object, **kw: object) -> bool:
        done = await write(self, key, value, **kw)
        self.kv[EARLIER] = theirs
        return done

    with monkeypatch.context() as m:
        if gap == "before the write":
            m.setattr(Config, "get_config", swap_after_read)
        else:
            m.setattr(Config, "set_config", swap_after_write)
        assert await STORE.clear(config, "org-a", "c1")

    assert config.kv[EARLIER] == theirs
    assert await STORE.list(config) == [theirs]
    assert await STORE.read(config, "org-b", "c1") == theirs


async def test_no_call_ever_writes_or_deletes_an_earlier_versions_key() -> None:
    class Guarded(Config):
        async def set_config(self, key: str, value: object, **kw: object) -> bool:
            assert key != EARLIER
            return await super().set_config(key, value, **kw)

        async def delete_config(self, key: str) -> bool:
            assert key != EARLIER
            return await super().delete_config(key)

    for owner in ("org-a", "org-b"):
        config = Guarded()
        config.kv[EARLIER] = {"orgId": owner, "connectorId": "c1", "requestedAt": 1}
        await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=2)
        await STORE.reschedule(config, {"orgId": "org-a", "connectorId": "c1", "requestedAt": 2}, next_attempt_at=9)
        assert await STORE.clear(config, "org-a", "c1")
        assert [i["orgId"] for i in await STORE.list(config)] == ([] if owner == "org-a" else ["org-b"])


async def test_a_later_delete_by_an_old_replica_is_pending_again() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    await STORE.clear(config, "org-a", "c1")
    config.kv[EARLIER] = later = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 5}
    assert await STORE.list(config) == [later]
    await STORE.reschedule(config, later, next_attempt_at=9)
    assert [i["attempts"] for i in await STORE.list(config)] == [1]


async def test_a_new_delete_after_a_settled_one_is_pending_and_clears() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    await STORE.clear(config, "org-a", "c1")
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=8)
    assert [i["requestedAt"] for i in await STORE.list(config)] == [8]
    assert await STORE.clear(config, "org-a", "c1") and await STORE.list(config) == []


async def test_a_marker_that_cannot_be_written_keeps_the_intent_pending() -> None:
    class Refuses(Config):
        async def set_config(self, key: str, value: object, **_kw: object) -> bool:
            return False

    config = Refuses()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    assert not await STORE.clear(config, "org-a", "c1")
    assert len(await STORE.list(config)) == 1


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
