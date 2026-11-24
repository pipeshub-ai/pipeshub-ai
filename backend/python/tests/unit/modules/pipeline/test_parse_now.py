"""Parse on demand: who is asked for, the event, the shared in-flight claim, the settings."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.pipeline import parse_now
from app.modules.pipeline.parse_now import (
    ParseNow,
    parse_outcome,
    terminal_marker_status,
)
from app.services.featureflag.platform_settings import parse_now_wait_seconds
from app.services.graph_db.common.record_visibility import (
    is_readable_record,
    is_searchable_record,
)

_LOG = logging.getLogger("test_parse_now")


def _doc(status: str | None = "NOT_STARTED", **extra: object) -> dict[str, object]:
    return {"indexingStatus": status, "contentRev": "rev-1", "connectorId": "kb-1", "mimeType": "text/plain", **extra}


class _FakeRedis:
    """Just ``SET NX EX`` and ``DEL``, shared by every ``ParseNow`` in a test."""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.deleted: list[str] = []

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        assert nx and ex
        if key in self.keys:
            return False
        self.keys[key] = value
        return True

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.keys.pop(key, None)


def _asker(*, sent: object = True, redis: _FakeRedis | None = None) -> tuple[ParseNow, AsyncMock]:
    producer = MagicMock()
    producer.send_message = AsyncMock(return_value=sent)
    provider = SimpleNamespace(get_client=lambda: redis, key_namespace="") if redis else None
    return ParseNow(MagicMock(), _LOG, producer, provider), producer.send_message


@pytest.fixture
def redis() -> _FakeRedis:
    return _FakeRedis()


# --- What the record says ---------------------------------------------------------


@pytest.mark.parametrize(("doc", "readable"), [
    (_doc("COMPLETED"), True),
    (_doc("QUEUED", hasIndexedCopy=True), True),
    (_doc("QUEUED", parseNowStatus="STORED", parseNowRev="rev-1"), True),
    (_doc("QUEUED", parseNowStatus="STORED", parseNowRev="rev-0"), False),
    (_doc("QUEUED", parseNowStatus="STORED"), False),
    (_doc("QUEUED", parseNowStatus="QUEUED", parseNowRev="rev-1"), False),
    (_doc("QUEUED", contentRev=None, parseNowStatus="STORED", parseNowRev=None), True),
    (_doc("QUEUED", contentRev=None, parseNowStatus="STORED", parseNowRev="rev-1"), False),
])
def test_a_record_parsed_on_demand_is_readable_for_its_revision_only(doc, readable) -> None:
    assert is_readable_record(doc) is readable


def test_search_is_not_widened_by_a_parse_on_demand() -> None:
    assert is_searchable_record(_doc("QUEUED", parseNowStatus="STORED", parseNowRev="rev-1")) is False


@pytest.mark.parametrize(("marker", "status"), [
    ("PASSWORD_PROTECTED", "PASSWORD_PROTECTED"),
    ("FILE_TYPE_NOT_SUPPORTED", "FILE_TYPE_NOT_SUPPORTED"),
    ("EMPTY", "EMPTY"),
    ("NEEDS_MULTIMODAL", "ENABLE_MULTIMODAL_MODELS"),
])
def test_a_parse_that_ended_for_good_is_known_for_its_revision(marker, status) -> None:
    assert terminal_marker_status(_doc(parseNowStatus=marker, parseNowRev="rev-1")) == status
    assert terminal_marker_status(_doc(parseNowStatus=marker, parseNowRev="rev-0")) is None


def test_a_failed_parse_ends_the_wait_but_may_be_tried_again_by_the_next_read() -> None:
    failed = _doc("QUEUED", parseNowStatus="FAILED", parseNowRev="rev-1")
    assert terminal_marker_status(failed) is None
    assert parse_outcome(failed) == "FAILED"


def test_a_parse_still_going_has_no_outcome() -> None:
    assert parse_outcome(_doc("QUEUED", parseNowStatus="IN_PROGRESS", parseNowRev="rev-1")) is None
    assert parse_outcome(_doc("QUEUED", parseNowStatus="STORED", parseNowRev="rev-1")) == parse_now.READY
    assert parse_outcome(_doc("PASSWORD_PROTECTED")) == "PASSWORD_PROTECTED"


# --- Who is eligible --------------------------------------------------------------


@pytest.mark.parametrize("status", ["NOT_STARTED", "QUEUED", "IN_PROGRESS", "AUTO_INDEX_OFF", "FAILED"])
async def test_waiting_statuses_are_eligible(status) -> None:
    asker, _ = _asker()
    assert await asker.assess(MagicMock(), _doc(status)) == (True, status)


@pytest.mark.parametrize("status", [
    "PASSWORD_PROTECTED", "FILE_TYPE_NOT_SUPPORTED", "EMPTY", "ENABLE_MULTIMODAL_MODELS", "SKIPPED", "COMPLETED", None,
])
async def test_other_statuses_are_not(status) -> None:
    asker, _ = _asker()
    can, _ = await asker.assess(MagicMock(), _doc(status))
    assert can is False


async def test_a_trashed_record_is_not_eligible() -> None:
    asker, _ = _asker()
    assert (await asker.assess(MagicMock(), _doc("QUEUED", isDeleted=True)))[0] is False


async def test_a_parse_that_ended_for_good_is_described_by_its_own_status() -> None:
    asker, _ = _asker()
    doc = _doc("NOT_STARTED", parseNowStatus="PASSWORD_PROTECTED", parseNowRev="rev-1")
    assert await asker.assess(MagicMock(), doc) == (False, "PASSWORD_PROTECTED")


@pytest.mark.parametrize(("app", "eligible"), [
    ({"id": "kb-1", "isActive": True}, True),
    ({"id": "kb-1", "isActive": False}, False),
    (None, False),
])
async def test_a_connector_record_needs_its_connector_on(app, eligible) -> None:
    asker, _ = _asker()
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=app)

    can, _ = await asker.assess(graph, _doc("QUEUED", origin="CONNECTOR"))

    assert can is eligible


async def test_an_unreadable_connector_is_not_eligible() -> None:
    asker, _ = _asker()
    graph = MagicMock()
    graph.get_document = AsyncMock(side_effect=RuntimeError("graph down"))

    assert (await asker.assess(graph, _doc("QUEUED", origin="CONNECTOR")))[0] is False


# --- The event --------------------------------------------------------------------


async def test_one_event_goes_to_the_priority_topic_with_the_callers_org(redis) -> None:
    asker, send = _asker(redis=redis)

    assert await asker.request("rec-1", _doc("QUEUED", orgId="row-org", virtualRecordId="vr-1"), "org-1", "user-1") is True

    send.assert_awaited_once()
    topic, event = send.await_args.args
    assert topic == "record-events-priority" and send.await_args.kwargs == {"key": "rec-1"}
    assert event["eventType"] == "reindexRecord"
    assert event["payload"] == {
        "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": "vr-1", "connectorId": "kb-1",
        "connectorName": None, "mimeType": "text/plain", "parseNow": True, "requestedByUserId": "user-1",
        "contentRev": "rev-1",
    }


async def test_a_refused_publish_is_not_an_exception(redis) -> None:
    asker, _ = _asker(sent=False, redis=redis)
    assert await asker.request("rec-1", _doc("QUEUED"), "org-1", "user-1") is False


# --- The in-flight claim (several readers, several replicas) -----------------------


async def test_five_readers_of_one_record_cause_one_event(redis) -> None:
    askers = [_asker(redis=redis) for _ in range(5)]  # as if five replicas

    answers = [await a.request("rec-1", _doc("QUEUED"), "org-1", f"user-{i}") for i, (a, _) in enumerate(askers)]

    assert answers == [True] * 5  # all of them have a parse to wait for
    assert sum(send.await_count for _, send in askers) == 1


async def test_another_org_is_a_different_claim_but_a_new_revision_is_not(redis) -> None:
    asker, send = _asker(redis=redis)

    await asker.request("rec-1", _doc("QUEUED"), "org-1", "u")
    # The revision is assigned once indexing starts; a late reader must not ask a second time.
    await asker.request("rec-1", _doc("QUEUED", contentRev="rev-2"), "org-1", "u")
    await asker.request("rec-1", _doc("QUEUED"), "org-2", "u")

    assert send.await_count == 2


async def test_a_claim_that_ran_out_lets_the_next_reader_ask_again(redis) -> None:
    asker, send = _asker(redis=redis)
    await asker.request("rec-1", _doc("QUEUED"), "org-1", "u")
    redis.keys.clear()  # the TTL passed

    await asker.request("rec-1", _doc("QUEUED"), "org-1", "u")

    assert send.await_count == 2


@pytest.mark.parametrize("sent", [False, "raises"])
async def test_a_failed_publish_gives_the_claim_back(redis, sent) -> None:
    asker, send = _asker(sent=False, redis=redis)
    if sent == "raises":
        send.side_effect = RuntimeError("broker down")

    assert await asker.request("rec-1", _doc("QUEUED"), "org-1", "u") is False

    assert redis.keys == {} and len(redis.deleted) == 1
    send.side_effect = None
    send.return_value = True
    assert await asker.request("rec-1", _doc("QUEUED"), "org-1", "u") is True


async def test_without_redis_the_event_is_sent_anyway() -> None:
    asker, send = _asker()

    assert await asker.request("rec-1", _doc("QUEUED"), "org-1", "u") is True

    send.assert_awaited_once()


# --- The settings ----------------------------------------------------------------


@pytest.mark.parametrize(("settings", "seconds"), [
    ({}, 55),
    ({"parseNowWaitSeconds": 30}, 30),
    ({"parseNowWaitSeconds": 0}, 0),
    ({"parseNowWaitSeconds": 500}, 55),
    ({"parseNowWaitSeconds": -4}, 0),
    ({"parseNowWaitSeconds": True}, 55),
    ({"parseNowWaitSeconds": 0.4}, 1),
    ({"parseNowWaitSeconds": "30"}, 55),
    ({"parseNowWaitSeconds": None}, 55),
    ({"parseNowEnabled": True}, 55),
    ({"parseNowEnabled": False}, 0),
    ({"parseNowEnabled": False, "parseNowWaitSeconds": 30}, 0),
])
async def test_the_wait_comes_from_the_platform_settings(settings, seconds) -> None:
    config = MagicMock()
    config.get_config = AsyncMock(return_value=settings)

    assert await parse_now_wait_seconds(config) == seconds


async def test_unreadable_settings_give_the_default() -> None:
    config = MagicMock()
    config.get_config = AsyncMock(side_effect=RuntimeError("store down"))

    assert await parse_now_wait_seconds(config) == 55
    assert await parse_now_wait_seconds(None) == 55


async def test_the_wait_is_read_through_the_config_cache() -> None:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={})

    await parse_now_wait_seconds(config)

    assert config.get_config.await_args.kwargs["use_cache"] is True


async def test_a_dead_broker_does_not_stall_the_read(monkeypatch) -> None:
    async def hang(*_a, **_kw) -> None:
        await asyncio.sleep(30)

    monkeypatch.setattr(parse_now, "_PRODUCER_TIMEOUT_SECONDS", 0.05)
    asker, send = _asker()
    send.side_effect = hang

    assert await asker.request("rec-1", _doc("QUEUED"), "org-1", "u") is False
