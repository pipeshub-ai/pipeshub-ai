"""ERR-UX2 EU-O3 and EU-O5: a rate-limited provider's wait honours its Retry-After, capped; today's loop shows it as
the durable path does (contracts/harness/vectors/provider-wait.json)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.agent_loop.hooks.retry_with_status import retry_with_status
from app.agents.agent_loop.protocol.formatter import AGUI_FORMATTER, LEGACY_FORMATTER
from app.harness.assembly.model import RATE_LIMIT_BACKOFF_S, ClassifiedModelPort
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.durable.ports import ModelBackoff
from harness_core.models.retry_after import PROVIDER_WAIT_CAP_S, provider_wait_s
from harness_core.models.transports.base import RetryConfig

VECTOR = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "provider-wait.json").read_text()
)
NOW = datetime(2026, 10, 11, 12, 0, 0, tzinfo=UTC)


class _Response:
    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


class RateLimitError(Exception):
    """As the OpenAI SDK raises it: the 429's response, headers included, on the exception."""

    def __init__(self, headers: dict[str, str]) -> None:
        super().__init__("Error code: 429 - rate_limit_exceeded")
        self.response = _Response(headers)
        self.status_code = 429


def _wrapped(headers: dict[str, str]) -> TransportError:
    try:
        raise RateLimitError(headers)
    except RateLimitError as sdk:
        error = TransportError("rate limited", status_code=429, retryable=True)
        error.__cause__ = sdk
        return error


CASES = [
    ("seconds", {"retry-after": "7"}, 7.0),
    ("milliseconds first", {"retry-after-ms": "2500", "retry-after": "9"}, 2.5),
    ("http date", {"Retry-After": "Sun, 11 Oct 2026 12:00:30 GMT"}, 30.0),
    ("past date", {"retry-after": "Sun, 11 Oct 2026 11:00:00 GMT"}, 0.0),
    ("capped", {"retry-after": "3600"}, PROVIDER_WAIT_CAP_S),
    ("unreadable", {"retry-after": "soon"}, RATE_LIMIT_BACKOFF_S),
    ("absent", {}, RATE_LIMIT_BACKOFF_S),
]


@pytest.mark.parametrize(("headers", "wait"), [(c[1], c[2]) for c in CASES], ids=[c[0] for c in CASES])
def test_eu_o3_the_wait_is_the_provider_s_within_the_cap(headers: dict[str, str], wait: float) -> None:
    assert provider_wait_s(_wrapped(headers), now=NOW, default_s=RATE_LIMIT_BACKOFF_S) == pytest.approx(wait)


async def test_eu_o3_a_durable_run_yields_for_the_provider_s_wait() -> None:
    class Inner:
        async def sample(self, call: object, on_delta: object) -> Any:  # noqa: ANN401
            raise RateLimitError({"retry-after": "7"})

    port = ClassifiedModelPort(Inner(), clock=SimpleNamespace(now=lambda: NOW))  # type: ignore[arg-type]
    with pytest.raises(ModelBackoff) as raised:
        _ = await port.sample(object(), object())  # type: ignore[arg-type]
    assert raised.value.retry_after_s == pytest.approx(7.0)


async def test_eu_o5_today_s_loop_waits_as_asked_and_shows_the_durable_frame() -> None:
    written: list[dict[str, Any]] = []
    slept: list[float] = []

    class Sink:
        async def write(self, event: dict[str, Any]) -> None:
            written.append(event)

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    context = SimpleNamespace(run_id="run-1")
    calls = 0

    async def model() -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _wrapped({"retry-after": "7"})
        return "answer"

    wrapper = retry_with_status(
        Sink(),  # type: ignore[arg-type]
        RetryConfig(max_retries=2),
        wait_frames=lambda retry_at, message: AGUI_FORMATTER.provider_wait(context, retry_at=retry_at, message=message),
        clock=SimpleNamespace(now=lambda: NOW),  # type: ignore[arg-type]
        sleep=sleep,
    )
    assert await wrapper(model) == "answer"  # type: ignore[arg-type]
    assert slept == [7.0]
    [frame] = written
    data = frame["data"] if "data" in frame else frame
    assert data["type"] == VECTOR["frameType"]
    assert data["snapshot"] == {"status": VECTOR["status"], VECTOR["retryAtField"]: "2026-10-11T12:00:07.000Z"}
    assert "rate limit" not in json.dumps(frame).lower(), "the web app words the wait; no server text"


def test_eu_o5_the_legacy_protocol_keeps_its_status_event() -> None:
    frames = LEGACY_FORMATTER.provider_wait(SimpleNamespace(run_id="r"), retry_at="2026-10-11T12:00:07.000Z", message="m")
    assert frames == [{"event": "status", "data": {"status": "retrying", "message": "m"}}]
