"""The fleet answer for keeping the old copy on the PostgreSQL broker, against real queue claims.

The broker keeps no member list, so the answer is the operator's statement that no older pod runs plus the live claims: a working indexing
consumer without the protocol tag refuses the swap, a consumer of another service (its name is never tagged) does not.

Needs ``PG_RUNTIME_TEST_URL`` (see conftest).
"""
from __future__ import annotations

import uuid

import pytest

from app.plugins.telemetry.modules import indexing_metrics
from app.modules.indexing import keep_old_copy
from app.services.messaging import config as mconfig
from app.services.messaging import membership
from app.services.messaging.postgres.queue import PgQueue
from app.services.messaging.storage_protocol import tagged

pytestmark = pytest.mark.integration


@pytest.fixture
async def world(runtime: object, monkeypatch: pytest.MonkeyPatch, uid: str) -> tuple[PgQueue, str, list[str]]:
    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    monkeypatch.setenv(keep_old_copy.ENV_PG_BROKER_CONFIRMED, "on")
    monkeypatch.setattr(mconfig, "get_message_broker_type", lambda: mconfig.MessageBrokerType.POSTGRES)
    keep_old_copy._refusals_logged.clear()
    reasons: list[str] = []
    monkeypatch.setattr(indexing_metrics.SWAP_REFUSALS, "inc", lambda reason: reasons.append(reason))
    queue = PgQueue(runtime)
    group = f"records-{uid}"
    monkeypatch.setattr(membership, "_registered", {group: {f"record-events-{uid}"}})
    monkeypatch.setattr(membership, "_expected", {group: {f"record-events-{uid}"}})
    return queue, group, reasons


async def _claim(queue: PgQueue, topic: str, group: str, consumer: str) -> None:
    await queue.ensure_group(topic, group, from_start=False)
    await queue.publish(topic, [(f"k-{uuid.uuid4().hex[:6]}", "{}")])
    assert await queue.claim(group, [topic], consumer, 1, 300), "no delivery claimed"


async def test_only_current_indexing_pods_allow_the_swap(world, uid: str) -> None:
    queue, group, reasons = world
    await _claim(queue, f"record-events-{uid}", group, tagged(f"indexing-{uid}"))
    assert await keep_old_copy.swap_allowed(object()) is True
    assert reasons == []


async def test_an_older_indexing_pod_with_a_live_claim_refuses_it(world, uid: str) -> None:
    queue, group, reasons = world
    await _claim(queue, f"record-events-{uid}", group, f"indexing-{uid}/sp3")
    assert await keep_old_copy.swap_allowed(object()) is False
    assert reasons == ["older_consumer"]


async def test_a_connector_consumer_that_is_working_does_not_refuse_it(world, uid: str) -> None:
    """The connector service's consumers are not indexing consumers and never carry the tag."""
    queue, group, reasons = world
    await _claim(queue, f"record-events-{uid}", group, tagged(f"indexing-{uid}"))
    await _claim(queue, f"sync-events-{uid}", f"sync-{uid}", f"connectors-sync-{uid}")
    assert await keep_old_copy.swap_allowed(object()) is True, reasons


async def test_without_the_operators_statement_the_swap_is_refused_whatever_the_claims_say(world, uid: str, monkeypatch: pytest.MonkeyPatch) -> None:
    queue, group, reasons = world
    await _claim(queue, f"record-events-{uid}", group, tagged(f"indexing-{uid}"))
    monkeypatch.delenv(keep_old_copy.ENV_PG_BROKER_CONFIRMED)
    assert await keep_old_copy.swap_allowed(object()) is False
    assert reasons == ["pg_broker_unconfirmed"]
