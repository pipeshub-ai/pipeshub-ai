"""Telemetry collects only when the deployment flag is on AND an admin switched
Data Collection on: nothing may be recorded that the admin cannot see or stop."""

import asyncio
from collections.abc import Iterator
from typing import NoReturn
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.telemetry import flag
from app.telemetry.backend import PrometheusBackend
from app.telemetry.event_buffer import event_buffer, record_event
from app.telemetry.middleware import MetricsMiddleware
from app.telemetry.pusher import MetricsPusher


@pytest.fixture(autouse=True)
def _consent_back_on() -> Iterator[None]:
    yield
    flag.set_collection_consent(True)
    event_buffer.drain()


@pytest.mark.parametrize(
    ("flag_value", "consent", "expected"),
    [("true", True, True), ("true", False, False), ("false", True, False), ("false", False, False)],
)
def test_collecting_needs_both_the_flag_and_the_consent(monkeypatch, flag_value, consent, expected) -> None:
    monkeypatch.setenv(flag.TELEMETRY_COLLECTION_FLAG, flag_value)
    flag.set_collection_consent(consent)
    assert flag.telemetry_collecting() is expected


def test_no_metric_is_recorded_while_consent_is_off_and_again_once_on() -> None:
    backend = PrometheusBackend()
    counter = backend.counter("consent_test_total", "h", ["k"])
    gauge = backend.gauge("consent_test_gauge", "h", ["k"])
    histogram = backend.histogram("consent_test_seconds", "h", ["k"], [1.0])

    flag.set_collection_consent(False)
    counter.inc("a")
    gauge.set("a", value=5)
    histogram.observe("a", value=0.5)
    assert 'k="a"' not in backend.serialize()

    flag.set_collection_consent(True)
    counter.inc("a")
    assert 'consent_test_total{k="a"} 1.0' in backend.serialize()


def test_events_are_dropped_while_consent_is_off() -> None:
    flag.set_collection_consent(False)
    record_event("search_performed")
    assert event_buffer.size() == 0


async def test_requests_pass_through_unrecorded_while_consent_is_off() -> None:
    flag.set_collection_consent(False)
    response = MagicMock(status_code=200)
    call_next = AsyncMock(return_value=response)
    middleware = MetricsMiddleware(app=MagicMock(), service_name="query")

    with patch("app.telemetry.middleware.HTTP_REQUESTS") as requests:
        assert await middleware.dispatch(MagicMock(), call_next) is response

    requests.inc.assert_not_called()


async def _one_tick(pusher: MetricsPusher) -> None:
    async def stop(_seconds: float) -> NoReturn:
        raise asyncio.CancelledError

    with patch("app.telemetry.pusher.asyncio.sleep", stop), pytest.raises(asyncio.CancelledError):
        await pusher._run()


def _pusher(consent: str) -> MetricsPusher:
    config = MagicMock()
    config.get_config = AsyncMock(return_value={
        "serverUrl": "http://collector/collect-metrics", "installId": "i", "apiKey": "k",
        "appVersion": "1", "pushIntervalMs": "300000", "enableMetricCollection": consent,
    })
    pusher = MetricsPusher(config, "query", MagicMock())
    pusher._push = AsyncMock()
    pusher._ship_events = AsyncMock()
    return pusher


async def test_switching_off_stops_collection_and_discards_what_was_held() -> None:
    flag.set_collection_consent(True)
    record_event("gathered_while_on")
    pusher = _pusher("false")

    await _one_tick(pusher)

    assert flag.telemetry_collecting() is False
    assert event_buffer.size() == 0
    pusher._push.assert_not_called()


async def test_switching_on_starts_collection_and_pushing() -> None:
    flag.set_collection_consent(False)
    pusher = _pusher("true")

    await _one_tick(pusher)

    assert flag.telemetry_collecting() is True
    pusher._push.assert_awaited_once()


async def test_consent_is_read_every_few_seconds_not_once_per_push_interval() -> None:
    pusher = _pusher("true")
    slept: list[float] = []

    async def record_sleep(seconds: float) -> NoReturn:
        slept.append(seconds)
        raise asyncio.CancelledError

    with patch("app.telemetry.pusher.asyncio.sleep", record_sleep), pytest.raises(asyncio.CancelledError):
        await pusher._run()

    assert slept == [5.0]
