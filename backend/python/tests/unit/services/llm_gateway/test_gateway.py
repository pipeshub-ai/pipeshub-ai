"""LLMGateway: cap, timeout, per-provider breaker with a real-call probe, and error typing."""

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    provider_key,
)
from app.utils.llm import LLMUnavailableError


class _Model:
    def __init__(self, *outcomes: object, delay: float = 0.0) -> None:
        self.outcomes = list(outcomes)
        self.delay = delay
        self.calls = 0
        self.running = 0
        self.peak = 0

    async def ainvoke(self, _messages: Any) -> Any:  # noqa: ANN401
        self.calls += 1
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if self.delay:
                await asyncio.sleep(self.delay)
            outcome = self.outcomes.pop(0) if self.outcomes else SimpleNamespace(content="ok", usage_metadata=None)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        finally:
            self.running -= 1


class _Status(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


@pytest.mark.asyncio
async def test_the_cap_holds_for_concurrent_calls() -> None:
    gateway, model = LLMGateway(2), _Model(delay=0.01)
    await asyncio.gather(*(gateway.invoke(model, [], provider="p") for _ in range(10)))
    assert model.calls == 10 and model.peak == 2


@pytest.mark.asyncio
async def test_a_hang_is_cut_off_as_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gateway_module, "CALL_TIMEOUT_S", 0.05)
    gateway = LLMGateway(1, failure_threshold=5)
    with pytest.raises(ProviderUnavailableError) as raised:
        await gateway.invoke(_Model(delay=5), [], provider="p")
    assert isinstance(raised.value, LLMUnavailableError)
    assert gateway.breaker("p")._consecutive_failures == 1


@pytest.mark.asyncio
async def test_an_open_circuit_fails_fast_without_calling_the_provider() -> None:
    gateway = LLMGateway(1, failure_threshold=2, cooldown_seconds=60)
    model = _Model(ConnectionRefusedError(), ConnectionRefusedError())
    for _ in range(2):
        with pytest.raises(ProviderUnavailableError):
            await gateway.invoke(model, [], provider="p")
    with pytest.raises(ProviderUnavailableError) as raised:
        await gateway.invoke(model, [], provider="p")
    assert model.calls == 2
    assert raised.value.retry_after is not None and 0 < raised.value.retry_after <= 60
    assert gateway.stats()["providers"]["p"]["state"] == "open"


@pytest.mark.asyncio
async def test_after_the_cooldown_one_real_call_probes_and_closes_the_circuit() -> None:
    gateway = LLMGateway(1, failure_threshold=1, cooldown_seconds=0.0)
    model = _Model(ConnectionRefusedError())
    with pytest.raises(ProviderUnavailableError):
        await gateway.invoke(model, [], provider="p")
    assert (await gateway.invoke(model, [], provider="p")).content == "ok"
    assert gateway.stats()["providers"]["p"]["state"] == "closed"


@pytest.mark.asyncio
async def test_circuits_are_per_provider() -> None:
    gateway = LLMGateway(1, failure_threshold=1, cooldown_seconds=60)
    with pytest.raises(ProviderUnavailableError):
        await gateway.invoke(_Model(ConnectionRefusedError()), [], provider="down")
    assert (await gateway.invoke(_Model(), [], provider="up")).content == "ok"


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [ValueError("not JSON"), _Status(400)])
async def test_a_request_error_is_raised_unchanged_and_not_held_against_the_provider(error: Exception) -> None:
    gateway = LLMGateway(1, failure_threshold=1)
    with pytest.raises(type(error)):
        await gateway.invoke(_Model(error), [], provider="p")
    assert gateway.stats()["providers"]["p"]["state"] == "closed"


@pytest.mark.asyncio
async def test_a_rate_limit_is_retried_outside_the_permit(monkeypatch: pytest.MonkeyPatch) -> None:
    slept: list[float] = []

    async def no_sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(gateway_module.asyncio, "sleep", no_sleep)
    gateway = LLMGateway(1)
    model = _Model(_Status(429))
    assert (await gateway.invoke(model, [], provider="p")).content == "ok"
    assert model.calls == 2 and len(slept) == 1 and gateway.limiter.in_use == 0


@pytest.mark.asyncio
async def test_a_rate_limit_that_persists_is_an_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway_module.asyncio, "sleep", no_sleep)
    with pytest.raises(ProviderUnavailableError):
        await LLMGateway(1).invoke(_Model(_Status(429), _Status(429), _Status(429)), [], provider="p")


def test_provider_keys_name_the_endpoint_and_never_the_credentials() -> None:
    llm = SimpleNamespace(model_name="gpt-x", openai_api_base="http://llm:8000/v1", openai_api_key="sk-secret")
    key = provider_key(llm)
    assert key == "SimpleNamespace:gpt-x@http://llm:8000/v1"
    assert "secret" not in key


@pytest.mark.asyncio
async def test_per_call_options_reach_the_model() -> None:
    seen: dict[str, object] = {}

    class _WithOptions:
        async def ainvoke(self, _messages: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            seen.update(kwargs)
            return SimpleNamespace(content="ok", usage_metadata=None)

    await LLMGateway(1).invoke(_WithOptions(), [], provider="p", max_tokens=4096, temperature=0)
    assert seen == {"max_tokens": 4096, "temperature": 0}


def test_the_plain_table_summary_names_rows_and_columns() -> None:
    from app.utils.table_enrichment import fallback_table_summary

    assert fallback_table_summary(["Name", "Age"], 3) == "A table with 3 rows and columns: Name, Age."
    assert fallback_table_summary([], 1) == "A table with 1 row and columns: unnamed columns."
