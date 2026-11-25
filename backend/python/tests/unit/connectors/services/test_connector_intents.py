"""One connector's cleanup is recorded under one key: an org never replaces or forgets another org's."""

import pytest

from app.connectors.services.connector_intents import (
    ConnectorIntentStore,
    held_by_another_org,
)

from .test_late_write_sweep import Config


class RefusedError(Exception):
    pass


STORE = ConnectorIntentStore("/services/cleanup/pending/", RefusedError, "cleanup")
KEY = "/services/cleanup/pending/c1"


async def test_another_orgs_intent_is_neither_replaced_nor_cleared() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1)
    before = dict(config.kv)

    with pytest.raises(RefusedError):
        await STORE.record(config, org_id="org-b", connector_id="c1", now_ms=2)
    assert not await STORE.clear(config, "c1", org_id="org-b")
    assert config.kv == before

    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=3)
    assert config.kv[KEY]["requestedAt"] == 3
    assert await STORE.clear(config, "c1", org_id="org-a") and config.kv == {}


async def test_an_earlier_intent_that_cannot_be_read_is_not_replaced() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1)
    config.read_error = ConnectionError("down")
    with pytest.raises(RefusedError):
        await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=2)
    assert config.kv[KEY]["requestedAt"] == 1


@pytest.mark.parametrize(("app", "other"), [
    ({"orgId": "org-b"}, True), ({"orgId": "org-a"}, False), ({"orgId": None}, False), ({}, False),
])
def test_an_app_with_no_org_is_not_another_orgs(app: dict, other: bool) -> None:
    assert held_by_another_org(app, "org-a") is other


async def test_another_orgs_intent_is_not_rescheduled_over() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1)
    before = dict(config.kv)
    assert not await STORE.reschedule(config, {"orgId": "org-b", "connectorId": "c1"}, next_attempt_at=9)
    assert config.kv == before


async def test_groups_added_since_the_intent_was_read_survive_a_reschedule() -> None:
    config = Config()
    await STORE.record(config, org_id="org-a", connector_id="c1", now_ms=1, recordGroupIds=["g1", "g2"])
    assert await STORE.reschedule(
        config, {"orgId": "org-a", "connectorId": "c1", "recordGroupIds": ["g1"]}, next_attempt_at=9,
    )
    assert config.kv[KEY]["recordGroupIds"] == ["g1", "g2"]
