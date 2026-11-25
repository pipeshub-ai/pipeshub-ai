"""TG-08 (C3.34): a connector's 429 on a read is tried again with backoff within the call's budget, then answered
as a structured ``rate_limited`` error with the service's retry-after.

Through the gateway as an engine reaches it: ``RunToolEndpoint`` over ``RegistryToolPort`` and ``ToolExecutor``.
A write is never tried again: a 429 there is answered at once.
"""

from __future__ import annotations

from typing import Any

import pytest

from harness_core.effects import EffectRunner, RequestEffectLog
from harness_core.effects.failures import retry_after_s, tool_error_code
from harness_core.gateway.endpoint import RunToolEndpoint
from harness_core.gateway.memory import InMemoryCitationSink
from harness_core.invoker import InvocationScope
from harness_core.kernel.durable import classify_tool_effect
from harness_core.testing.contracts.tool_endpoint import BINDING, claims_for
from harness_core.testing.fake_saas import RateLimited
from harness_core.testing.ids import SeededIds
from harness_core.tools.base import ParameterType, Tag, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.rate_limit import MAX_RETRIES, WAIT_BUDGET_S, RateLimitBudget
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder

_Q = [ToolParameter(name="q", type=ParameterType.STRING, description="Query", required=True)]


class LimitedSaas:
    """A connector that answers 429 ``limited`` times, then serves; each attempt is counted."""

    def __init__(self, limited: int, retry_after: float | None = 0.01) -> None:
        self.limited = limited
        self.retry_after = retry_after
        self.attempts: dict[str, int] = {}

    def _hit(self, name: str) -> None:
        self.attempts[name] = self.attempts.get(name, 0) + 1
        if self.attempts[name] <= self.limited:
            raise _Http429(self.retry_after) if self.retry_after is None else RateLimited(self.retry_after)

    @tool(path="/tools/crm/search", short_description="Search", description="Search the CRM.", parameters=_Q)
    async def search(self, q: str) -> str:
        self._hit("search")
        return f"found {q}"

    @tool(path="/tools/crm/create", short_description="Create", description="Create a record.", parameters=_Q)
    async def create(self, q: str) -> str:
        self._hit("create")
        return f"created {q}"


class _Response:
    def __init__(self, headers: dict[str, str]) -> None:
        self.status_code = 429
        self.headers = headers


class _Http429(Exception):
    """An HTTP client's error with no retry-after of its own."""

    def __init__(self, _: object = None) -> None:
        super().__init__("429 Too Many Requests")
        self.response = _Response({})


def limited_endpoint(saas: LimitedSaas) -> RunToolEndpoint:
    registry = ToolRegistry()
    ToolsetBuilder(saas, name="crm", description="CRM", path_prefix="/tools/crm").register_into(registry)
    effects = {
        "crm__search": classify_tool_effect("crm__search", [Tag("category", "read")]),
        "crm__create": classify_tool_effect("crm__create", [Tag("category", "write")]),
    }
    return RunToolEndpoint(
        binding=BINDING,
        catalog=RegistryToolPort(registry, effects),
        effects=EffectRunner(RequestEffectLog()),
        scope=InvocationScope(BINDING.run_id, BINDING.tenant, BINDING.attempt_id),
        ids=SeededIds(),
        citations=InMemoryCitationSink(),
    )


async def test_tg08_a_rate_limited_read_is_tried_again_and_answers() -> None:
    saas = LimitedSaas(limited=2)
    result = await limited_endpoint(saas).call_tool(claims_for(BINDING), "crm__search", {"q": "acme"})
    assert (result.is_error, result.text) == (False, "found acme")
    assert saas.attempts == {"search": 3}


async def test_tg08_past_the_retries_the_answer_is_a_structured_rate_limited_error() -> None:
    saas = LimitedSaas(limited=100)
    result = await limited_endpoint(saas).call_tool(claims_for(BINDING), "crm__search", {"q": "acme"})
    assert saas.attempts == {"search": MAX_RETRIES + 1}
    assert (result.is_error, result.disposition, result.retryable) == (True, "executed", True)
    assert (result.error_code, result.retry_after_s) == ("rate_limited", 0.01)
    structured = result.structured()
    assert (structured["errorCode"], structured["retryAfterS"]) == ("rate_limited", 0.01)
    assert "wait 0.01 s before calling it again" in result.text


async def test_tg08_a_retry_after_beyond_the_budget_is_answered_at_once() -> None:
    saas = LimitedSaas(limited=1, retry_after=WAIT_BUDGET_S + 1)
    result = await limited_endpoint(saas).call_tool(claims_for(BINDING), "crm__search", {"q": "acme"})
    assert saas.attempts == {"search": 1}, "no wait the budget cannot pay"
    assert (result.error_code, result.retry_after_s) == ("rate_limited", WAIT_BUDGET_S + 1)


async def test_tg08_a_rate_limited_write_is_never_tried_again() -> None:
    saas = LimitedSaas(limited=1)
    result = await limited_endpoint(saas).call_tool(claims_for(BINDING), "crm__create", {"q": "acme"})
    assert saas.attempts == {"create": 1}
    assert result.is_error and result.error_code == "rate_limited"


async def test_tg08_without_a_retry_after_the_waits_back_off_and_stay_in_budget() -> None:
    registry = ToolRegistry()
    saas = LimitedSaas(limited=100, retry_after=None)
    ToolsetBuilder(saas, name="crm", description="CRM", path_prefix="/tools/crm").register_into(registry)
    waits: list[float] = []

    async def sleep(seconds: float) -> None:
        waits.append(seconds)

    from harness_core.kernel.core.types import ToolCall

    executor = ToolExecutor(registry, sleep=sleep, rate_limits=RateLimitBudget(wait_budget_s=5.0))
    result = await executor.call_tool(ToolCall(id="c", name="crm__search", arguments={"q": "x"}), retry_rate_limited=True)
    assert waits == [1.0, 2.0], "1 s, then 2 s; 4 s more would pass the 5 s budget"
    assert (result.is_error, result.error_code, result.retry_after_s) == (True, "rate_limited", None)


def test_tg08_the_wait_never_eats_the_calls_time_bound() -> None:
    budget = RateLimitBudget()
    assert budget.affords(5.0, retries=0, waited_s=0.0, left_s=10.0)
    assert not budget.affords(9.5, retries=0, waited_s=0.0, left_s=10.0)
    assert not budget.affords(1.0, retries=MAX_RETRIES, waited_s=0.0, left_s=None)


@pytest.mark.parametrize(
    ("exc", "seconds"),
    [
        (RateLimited(2.5), 2.5),
        (type("E", (Exception,), {"response": _Response({"retry-after": "7"})})(), 7.0),
        (type("E", (Exception,), {"response": _Response({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})})(), None),
        (type("E", (Exception,), {"retry_after": -1})(), None),
    ],
)
def test_tg08_retry_after_is_read_from_the_client_or_the_response(exc: Exception, seconds: Any) -> None:  # noqa: ANN401
    assert retry_after_s(exc) == seconds


def test_tg08_the_fake_saas_rate_limit_is_typed_rate_limited() -> None:
    assert tool_error_code(RateLimited(1.0)) == "rate_limited"
