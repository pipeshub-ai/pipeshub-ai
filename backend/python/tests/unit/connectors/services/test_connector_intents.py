"""Every delete request has an entry of its own, and settling an intent removes what was read and nothing else."""

import pytest

from app.connectors.services.connector_intents import (
    KEYS,
    ConnectorIntentStore,
    held_by_another_org,
)

from .test_late_write_sweep import Config


class RefusedError(Exception):
    pass


STORE = ConnectorIntentStore("/services/cleanup/pending/", RefusedError, "cleanup")
EARLIER = "/services/cleanup/pending/c1"


async def record(config: Config, org: str = "org-a", at: int = 1, **fields: object) -> None:
    await STORE.record(config, org_id=org, connector_id="c1", now_ms=at, **fields)


async def pending(config: Config) -> list[tuple[str, int]]:
    return [(i["orgId"], i["requestedAt"]) for i in await STORE.list(config)]


async def read(config: Config, org: str = "org-a") -> dict:
    intent = await STORE.read(config, org, "c1")
    assert intent is not None
    return intent


@pytest.mark.parametrize("org", ["org-a", "org-b"])
async def test_a_delete_recorded_between_the_read_and_the_delete_stays_pending(
    monkeypatch: pytest.MonkeyPatch, org: str,
) -> None:
    """A newer delete, of the same org or another one, is recorded right before the entry that was read is deleted."""
    config = Config()
    await record(config)
    intent = await read(config)
    real, at = Config.delete_config, [5]

    async def racing(self: Config, *a: object, **kw: object) -> object:
        at[0] += 1
        await STORE.record(self, org_id=org, connector_id="c1", now_ms=at[0])
        return await real(self, *a, **kw)

    with monkeypatch.context() as m:
        m.setattr(Config, "delete_config", racing)
        assert await STORE.clear(config, intent)

    assert await pending(config) == [(org, 6)]
    assert all(key not in config.kv for key in intent[KEYS])


async def test_a_clear_deletes_only_the_entries_that_were_read() -> None:
    config = Config()
    await record(config, at=1)
    intent = await read(config)
    await record(config, at=2)
    await record(config, "org-b", at=3)
    newer = {k: v for k, v in config.kv.items() if v["requestedAt"] > 1}  # type: ignore[index]

    assert await STORE.clear(config, intent)

    assert config.kv == newer and await pending(config) == [("org-a", 2), ("org-b", 3)]


async def test_a_delete_recorded_during_a_reschedule_is_not_replaced() -> None:
    config = Config()
    await record(config, at=1)
    intent = await read(config)
    await record(config, at=2)
    assert await STORE.reschedule(config, intent, next_attempt_at=9)
    assert sorted(v["requestedAt"] for v in config.kv.values()) == [1, 2]  # type: ignore[index]
    assert (await read(config)).get("nextAttemptAt") is None  # the newer delete is not held back
    assert await STORE.clear(config, await read(config)) and config.kv == {}


async def test_a_retried_delete_is_one_intent_with_the_groups_of_every_attempt() -> None:
    config = Config()
    await record(config, at=1, recordGroupIds=["g1", "g2"])
    await record(config, at=2, recordGroupIds=["g2", "g3"])
    [intent] = await STORE.list(config)
    assert (intent["requestedAt"], intent["recordGroupIds"]) == (2, ["g2", "g3", "g1"])
    assert intent == await read(config)
    assert await STORE.clear(config, intent) and config.kv == {}


async def test_nothing_about_where_an_intent_was_read_is_stored() -> None:
    config = Config()
    await record(config)
    await STORE.reschedule(config, await read(config), next_attempt_at=9)
    [stored] = config.kv.values()
    assert stored == {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "attempts": 1, "nextAttemptAt": 9}


async def test_two_orgs_intents_for_one_connector_id_live_apart() -> None:
    config = Config()
    await record(config, "org-a", 1)
    await record(config, "org-b", 2)
    assert await pending(config) == [("org-a", 1), ("org-b", 2)]
    assert await STORE.clear(config, await read(config, "org-a"))
    assert await pending(config) == [("org-b", 2)]


async def test_an_earlier_versions_intent_is_pending_for_its_own_org_until_it_is_settled() -> None:
    config = Config()
    config.kv[EARLIER] = earlier = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "recordGroupIds": ["g1"]}
    assert await STORE.read(config, "org-b", "c1") is None
    intent = await read(config)

    for attempt in (1, 2):
        assert await STORE.reschedule(config, intent, next_attempt_at=9)
        intent = await read(config)
        assert (intent["attempts"], intent["recordGroupIds"]) == (attempt, ["g1"])
        assert len(config.kv) == 2  # rescheduled in one entry of its own

    assert await STORE.clear(config, intent)
    assert config.kv[EARLIER] == earlier  # left where it is
    assert await pending(config) == [] and await STORE.read(config, "org-a", "c1") is None


@pytest.mark.parametrize("owner", ["org-a", "org-b"])
async def test_no_call_ever_writes_or_deletes_an_earlier_versions_key(owner: str) -> None:
    class Guarded(Config):
        async def set_config(self, key: str, value: object, **kw: object) -> bool:
            assert key != EARLIER
            return await super().set_config(key, value, **kw)

        async def delete_config(self, key: str) -> bool:
            assert key != EARLIER
            return await super().delete_config(key)

    config = Guarded()
    config.kv[EARLIER] = {"orgId": owner, "connectorId": "c1", "requestedAt": 1}
    await record(config, at=2)
    await STORE.reschedule(config, await read(config), next_attempt_at=9)
    assert await STORE.clear(config, await read(config))
    assert await pending(config) == ([] if owner == "org-a" else [("org-b", 1)])


@pytest.mark.parametrize("theirs", [("org-b", 2), ("org-a", 2)])
async def test_an_old_replica_recording_another_delete_around_a_clear_loses_nothing(theirs: tuple[str, int]) -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    intent = await read(config)
    config.kv[EARLIER] = later = {"orgId": theirs[0], "connectorId": "c1", "requestedAt": theirs[1]}
    assert await STORE.clear(config, intent)
    assert config.kv[EARLIER] == later and await pending(config) == [theirs]


async def test_a_settled_entry_stays_settled_when_the_org_deletes_again() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "recordGroupIds": ["old"]}
    await STORE.clear(config, await read(config))
    await record(config, at=8)
    [intent] = await STORE.list(config)
    assert intent["requestedAt"] == 8 and "recordGroupIds" not in intent
    assert await STORE.clear(config, intent) and await pending(config) == []


async def test_a_settled_entry_that_cannot_be_written_keeps_the_intent_pending() -> None:
    class Refuses(Config):
        async def set_config(self, key: str, value: object, **_kw: object) -> bool:
            return False

    config = Refuses()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    assert not await STORE.clear(config, await read(config))
    assert await pending(config) == [("org-a", 1)]


async def test_an_intent_that_cannot_be_recorded_raises() -> None:
    class Refuses(Config):
        async def set_config(self, key: str, value: object, **_kw: object) -> bool:
            return False

    with pytest.raises(RefusedError):
        await record(Refuses())


async def test_an_unreadable_earlier_entry_does_not_keep_the_orgs_own_intent_from_settling() -> None:
    class Corrupt(Config):
        async def get_config(self, key: str, *a: object, **kw: object) -> object:
            if key == EARLIER and kw.get("raise_on_error"):
                raise ValueError("cannot decrypt")
            return await super().get_config(key, *a, **kw)

    config = Corrupt()
    config.kv[EARLIER] = "corrupt"
    await record(config)
    assert await STORE.clear(config, await read(config))
    assert config.kv == {EARLIER: "corrupt"}


async def test_an_intent_of_the_org_that_cannot_be_read_raises() -> None:
    config = Config()
    await record(config)
    config.read_error = ConnectionError("down")
    with pytest.raises(ConnectionError):
        await STORE.read(config, "org-a", "c1")


async def test_an_entry_another_completer_already_removed_counts_as_cleared() -> None:
    config = Config()
    await record(config)
    intent = await read(config)
    config.kv.clear()
    assert await STORE.clear(config, intent)


async def test_an_entry_whose_key_names_another_org_or_connector_is_not_listed() -> None:
    config = Config()
    config.kv["/services/cleanup/pending/org-b/c1/r"] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    config.kv["/services/cleanup/pending/org-a/c2/r"] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1}
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "other", "requestedAt": 1}
    assert await STORE.list(config) == []


async def test_a_malformed_group_list_does_not_stop_the_listing() -> None:
    config = Config()
    config.kv[EARLIER] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": 1, "recordGroupIds": 3}
    await STORE.record(config, org_id="org-b", connector_id="c2", now_ms=1)
    assert {i["orgId"] for i in await STORE.list(config)} == {"org-a", "org-b"}


@pytest.mark.parametrize(("app", "other"), [
    ({"orgId": "org-b"}, True), ({"orgId": "org-a"}, False), ({"orgId": None}, False), ({}, False),
])
def test_an_app_with_no_org_is_not_another_orgs(app: dict, other: bool) -> None:
    assert held_by_another_org(app, "org-a") is other


async def test_a_malformed_request_time_does_not_stop_the_listing() -> None:
    config = Config()
    config.kv["/services/cleanup/pending/org-a/c1/bad"] = {"orgId": "org-a", "connectorId": "c1", "requestedAt": "soon"}
    await record(config, at=2)
    await record(config, "org-b", at=3)
    assert await pending(config) == [("org-a", 2), ("org-b", 3)]
