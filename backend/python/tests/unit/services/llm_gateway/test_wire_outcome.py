"""The outcome of a model call is decided by what happened on the wire, never by the exception a wrapper raised."""

import asyncio
import contextlib

import httpx
import pytest

from app.services.llm_gateway import wire
from app.services.llm_gateway.fleet_breaker import FleetBreaker, size_class
from app.services.llm_gateway.fleet_redis import FleetRedis
from app.services.llm_gateway.gateway import LLMGateway, ProviderUnavailableError


class GoogleAPIError(Exception):
    """Stands for a wrapper exception the old name-based check misread (Gemini)."""


class _Weird(Exception):
    pass


def _runnable(*, status: int | None, raises: BaseException | None, sent: bool = True, delay: float = 0.0) -> object:
    class Fake:
        async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
            if sent:
                wire.note_request_sent()
            if delay:
                await asyncio.sleep(delay)
            if status is not None:
                wire.note_response(status)
            if raises is not None:
                raise raises
            return "answer"

    return Fake()


# (wire status, sent, exception the wrapper raised, caller budget) -> does the breaker see a failure?
CASES = {
    "429-as-weird-exception": (429, True, _Weird("nope"), False, True),
    "503-as-google-error": (503, True, GoogleAPIError("unavailable"), False, True),
    "529-as-value-error": (529, True, ValueError("overloaded"), False, True),
    "500-with-no-exception-type-hint": (500, True, RuntimeError("x"), False, True),
    "connect-error-no-response": (None, True, httpx.ConnectError("refused"), False, True),
    "reset-no-response": (None, True, ConnectionResetError("reset"), False, True),
    "hang-at-the-ceiling": (None, True, TimeoutError(), False, True),
    "200-then-body-never-arrives": (200, True, httpx.ReadTimeout("slow"), False, True),
    "400-named-like-an-outage": (400, True, GoogleAPIError("Unavailable"), False, False),
    "404-rate-limit-named": (404, True, type("RateLimitError", (Exception,), {})("x"), False, False),
    "422": (422, True, ValueError("bad"), False, False),
    "200": (200, True, None, False, False),
    # The caller's budget elapses while the request waits: neutral, not the provider's failure.
    "caller-budget-timeout": (None, True, None, True, False),
    # The provider times out with the caller's budget far from spent: a failure.
    "provider-timeout-with-budget-left": (None, True, TimeoutError(), True, True),
}
CALLER_BUDGET_S = 0.05


@pytest.mark.parametrize("case", list(CASES), ids=list(CASES))
async def test_outcome_is_decided_by_the_wire(case: str, fleet: FleetRedis) -> None:
    status, sent, raises, caller_budget, is_failure = CASES[case]
    gateway = LLMGateway(4)
    waits = case == "caller-budget-timeout"
    runnable = _runnable(status=status, raises=raises, sent=sent, delay=CALLER_BUDGET_S * 10 if waits else 0.0)
    budget = (CALLER_BUDGET_S if waits else 5.0) if caller_budget else None
    call = gateway.invoke(runnable, "hi", provider="p:m@e", timeout=budget)
    if is_failure:
        with pytest.raises(ProviderUnavailableError) as seen:
            await call
        assert seen.value.sent is True
    elif waits:
        with pytest.raises(TimeoutError):
            await call
    elif raises is None:
        assert await call == "answer"
    else:
        with pytest.raises(type(raises)):  # the request's fault comes back unchanged
            await call
    # what the fleet breaker was told: five failures in a row open it, anything else leaves it closed
    breaker = FleetBreaker(fleet)
    from app.services.llm_gateway.fleet_breaker import provider_pk

    pk, cls = provider_pk("p:m@e"), size_class(1)
    for _ in range(4):
        gateway2 = LLMGateway(4)
        with contextlib.suppress(Exception):
            await gateway2.invoke(_runnable(status=status, raises=raises, sent=sent), "hi", provider="p:m@e", timeout=5.0 if caller_budget else None)
    admission = await breaker.admit(pk, cls, "probe")
    assert (admission.code != 0) == is_failure


@pytest.mark.parametrize("hang_status", [None])
async def test_a_hang_at_the_gateway_ceiling_is_a_failure(monkeypatch: pytest.MonkeyPatch, fleet: FleetRedis, hang_status: None) -> None:
    from app.services.llm_gateway import gateway as gw

    monkeypatch.setattr(gw, "CALL_TIMEOUT_S", 0.05)

    class Hangs:
        async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
            wire.note_request_sent()
            await asyncio.sleep(30)

    with pytest.raises(ProviderUnavailableError) as seen:
        await LLMGateway(1).invoke(Hangs(), "hi", provider="p:m")
    assert seen.value.sent is True


def test_the_judge_reads_the_wire_not_the_exception() -> None:
    w = wire.WireOutcome(sent=True, status=503)
    assert w.judge(raised=ValueError("x")) == wire.FAIL
    assert wire.WireOutcome(sent=True, status=None).judge(raised=None, caller_budget_expired=True) == wire.NEUTRAL
    assert wire.WireOutcome(sent=True, status=None).judge(raised=TimeoutError()) == wire.FAIL
    assert wire.WireOutcome(sent=True, status=400).judge(raised=RuntimeError()) == wire.OK_UNRATED
    assert wire.WireOutcome(sent=True, status=200).judge(raised=None) == wire.OK
    assert wire.WireOutcome(sent=False).judge(raised=RuntimeError()) == wire.UNSENT


def test_an_sdk_retry_decides_the_call_by_its_last_answer() -> None:
    with wire.wire_scope() as seen:
        wire.note_request_sent()
        wire.note_response(429)
        wire.note_request_sent()
        wire.note_response(200)
    assert seen.requests == 2 and seen.judge(raised=None) == wire.OK
    with wire.wire_scope() as seen:
        wire.note_request_sent()
        wire.note_response(200)
        wire.note_request_sent()
    assert seen.judge(raised=TimeoutError()) == wire.FAIL


async def test_the_hooks_cap_the_connect_timeout_and_never_raise() -> None:
    request = httpx.Request("GET", "http://x", extensions={"timeout": {"connect": 120.0, "read": 600.0, "write": 5.0, "pool": 5.0}})
    wire._request_hook(request)
    assert request.extensions["timeout"]["connect"] == wire.CONNECT_TIMEOUT_CAP_S
    assert request.extensions["timeout"]["read"] == 600.0
    unbounded = httpx.Request("GET", "http://x", extensions={"timeout": {"connect": None, "read": None, "write": None, "pool": None}})
    wire._request_hook(unbounded)
    assert unbounded.extensions["timeout"]["connect"] == wire.CONNECT_TIMEOUT_CAP_S
    short = httpx.Request("GET", "http://x", extensions={"timeout": {"connect": 2.0}})
    wire._request_hook(short)
    assert short.extensions["timeout"]["connect"] == 2.0


async def test_the_requests_of_a_failed_call_are_counted_from_the_wire(fleet) -> None:
    """An SDK that retries on its own puts three requests on the wire for one call; the error says so."""

    class Retrying:
        async def ainvoke(self, value, /, **kwargs):  # noqa: ANN202
            for _ in range(3):
                wire.note_request_sent()
                wire.note_response(503)
            raise RuntimeError("503 after retries")

    with pytest.raises(ProviderUnavailableError) as seen:
        await LLMGateway(2).invoke(Retrying(), "hi", provider="p:m@e")
    assert seen.value.requests == 3 and seen.value.sent is True
