"""D-180: the API tells a user's Force reindex from a plain one, and carries it into the events it publishes."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.api.router import mark_user_forced_reindex, reindex_single_record


def _request(body: dict | None) -> MagicMock:
    req = MagicMock()
    user = {"userId": "user-1", "orgId": "org-1", "role": "member"}
    req.state.user.get = lambda k, default=None: user.get(k, default)
    req.headers.get = lambda k, default=None: None
    req.json = AsyncMock(return_value=body if body is not None else {})
    return req


async def _published(body: dict | None, topic: str) -> dict:
    gp = AsyncMock()
    gp.get_document.return_value = None                      # the connector lock check takes its no-lock path
    gp.reindex_single_record = AsyncMock(return_value={
        "success": True,
        "eventData": {"eventType": "newRecord", "topic": topic, "payload": {"recordId": "rec-1"}},
        "recordId": "rec-1", "recordName": "doc.pdf", "connector": "c1", "userRole": "admin",
    })
    kafka = AsyncMock()
    kafka.publish_event = AsyncMock()
    await reindex_single_record("rec-1", _request(body), graph_provider=gp, kafka_service=kafka)
    return kafka.publish_event.await_args.args[1]["payload"]


async def test_a_user_force_reindex_of_one_record_publishes_a_forced_refresh() -> None:
    payload = await _published({"depth": 0, "force": True}, "record-events")
    assert payload["forceReindex"] is True
    assert payload["refreshPaidOutputs"] is True


@pytest.mark.parametrize("body", [{"depth": 0}, {"depth": 0, "force": False}, {"depth": 0, "force": "true"}, {"depth": 0, "force": 1}, None])
async def test_a_plain_reindex_publishes_neither_flag(body: dict | None) -> None:
    payload = await _published(body, "record-events")
    assert "forceReindex" not in payload
    assert "refreshPaidOutputs" not in payload


async def test_a_user_force_reindex_that_goes_through_the_connector_carries_force_for_the_walker() -> None:
    payload = await _published({"depth": 0, "force": True}, "sync-events")
    assert payload["force"] is True
    assert "refreshPaidOutputs" not in payload                 # set per record by the walker, not on the walk's event


async def test_a_plain_reindex_through_the_connector_carries_no_force() -> None:
    assert "force" not in await _published({"depth": 0}, "sync-events")


def test_marking_ignores_an_event_without_a_payload() -> None:
    event = {"topic": "record-events"}
    mark_user_forced_reindex(event)
    assert event == {"topic": "record-events"}


async def _forced_route(body: dict | None, monkeypatch: pytest.MonkeyPatch, *, write: bool) -> tuple[AsyncMock, dict, list[str]]:
    if write:
        monkeypatch.setenv("REFRESH_INTENT_WRITE", "true")
    else:
        monkeypatch.delenv("REFRESH_INTENT_WRITE", raising=False)
    order: list[str] = []
    gp = AsyncMock()
    gp.get_document.return_value = None
    gp.reindex_single_record = AsyncMock(return_value={
        "success": True,
        "eventData": {"eventType": "newRecord", "topic": "record-events", "payload": {"recordId": "rec-1"}},
        "recordId": "rec-1", "recordName": "doc.pdf", "connector": "c1", "userRole": "admin",
    })
    gp.update_record_fields_for_ids = AsyncMock(side_effect=lambda *_a, **_k: order.append("write") or 1)
    kafka = AsyncMock()
    kafka.publish_event = AsyncMock(side_effect=lambda *_a, **_k: order.append("publish"))
    await reindex_single_record("rec-1", _request(body), graph_provider=gp, kafka_service=kafka)
    return gp, kafka.publish_event.await_args.args[1]["payload"], order


async def test_a_forced_knowledge_base_file_stores_its_request_before_the_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    gp, payload, order = await _forced_route({"depth": 0, "force": True}, monkeypatch, write=True)
    ids, fields = gp.update_record_fields_for_ids.await_args.args
    assert ids == ["rec-1"] and fields["refreshIntentId"] == payload["refreshIntentId"] and fields["refreshIntentAtMs"] > 0
    assert order == ["write", "publish"]


async def test_a_forced_knowledge_base_file_stores_nothing_while_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    gp, payload, order = await _forced_route({"depth": 0, "force": True}, monkeypatch, write=False)
    gp.update_record_fields_for_ids.assert_not_awaited()
    assert "refreshIntentId" not in payload and order == ["publish"]       # no id while the writes are off: an older worker keys the purge on the delivery id


async def test_a_plain_knowledge_base_reindex_stores_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    gp, payload, _ = await _forced_route({"depth": 0}, monkeypatch, write=True)
    gp.update_record_fields_for_ids.assert_not_awaited()
    assert "refreshIntentId" not in payload
