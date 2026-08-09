"""`AppEventConsumer` is the last hop between a provider webhook and an
event-triggered workflow actually running.

It must commit (return True) for messages it *refuses* -- malformed or
chain-capped -- because those fail identically on every redelivery and would
otherwise park the stream. It must NOT commit for messages it merely failed
to process, or a transient store outage silently discards the event.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.services.events.consumer import AppEventConsumer
from app.services.events.models import CHAIN_DEPTH_CAP
from app.services.messaging.config import StreamMessage


class _RecordingEngine:
    def __init__(self, *, raises: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self._raises = raises

    async def fire_event(self, org_id: str, event_type: str, payload: dict[str, Any]) -> list:
        self.calls.append({"org_id": org_id, "event_type": event_type, "payload": payload})
        if self._raises:
            raise RuntimeError("trigger store unreachable")
        return []


def _message(**overrides: Any) -> StreamMessage:
    payload = {
        "org_id": "org-1",
        "event_type": "jira.issue.created",
        "payload": {"project": "OPS"},
        "dedupe_key": "evt-1",
        "chain_depth": 0,
    }
    payload.update(overrides)
    return StreamMessage(eventType="app_event", payload=payload)


class TestFanOut:
    @pytest.mark.asyncio
    async def test_an_event_reaches_fire_event_with_its_matching_fields(self) -> None:
        engine = _RecordingEngine()

        assert await AppEventConsumer(task_engine=engine).handle(_message()) is True

        assert len(engine.calls) == 1
        call = engine.calls[0]
        assert (call["org_id"], call["event_type"]) == ("org-1", "jira.issue.created")
        # The provider fields have to survive: they are what a trigger's
        # `event_filter` matches on.
        assert call["payload"]["project"] == "OPS"

    @pytest.mark.asyncio
    async def test_the_dedupe_key_is_carried_into_the_payload(self) -> None:
        """`fire_trigger`'s idempotency is what stops a webhook redelivery
        from running the workflow twice."""
        engine = _RecordingEngine()

        await AppEventConsumer(task_engine=engine).handle(_message())

        assert engine.calls[0]["payload"]["_dedupe_key"] == "evt-1"

    @pytest.mark.asyncio
    async def test_chain_depth_is_incremented_for_events_this_run_may_emit(self) -> None:
        engine = _RecordingEngine()

        await AppEventConsumer(task_engine=engine).handle(_message(chain_depth=2))

        assert engine.calls[0]["payload"]["_chain_depth"] == 3


class TestRefusals:
    @pytest.mark.asyncio
    async def test_an_event_at_the_chain_cap_is_dropped(self) -> None:
        """Workflow A emitting an event that fires workflow B that emits
        again is an infinite loop without this."""
        engine = _RecordingEngine()

        result = await AppEventConsumer(task_engine=engine).handle(
            _message(chain_depth=CHAIN_DEPTH_CAP),
        )

        assert result is True
        assert engine.calls == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("missing", ["org_id", "event_type"])
    async def test_an_unattributable_event_is_dropped_rather_than_guessed(self, missing: str) -> None:
        engine = _RecordingEngine()

        result = await AppEventConsumer(task_engine=engine).handle(_message(**{missing: ""}))

        assert result is True
        assert engine.calls == []

    @pytest.mark.asyncio
    async def test_a_failing_fan_out_asks_for_redelivery(self) -> None:
        """A store being briefly unreachable must not silently swallow the
        event. `_finalize_message` bounds retries and dead-letters, so a
        genuinely stuck message still cannot park the stream, and the fan-out
        is dedupe-keyed so the retry does not double-fire.
        """
        engine = _RecordingEngine(raises=True)

        assert await AppEventConsumer(task_engine=engine).handle(_message()) is False
