"""A stage run's spend is counted where it happens, once: failed sends by the job's send meter, charged sends per failed
attempt and per provider key, and the remaining allowance enforced on each request before it leaves."""

import asyncio
import contextlib

import httpx
import pytest

from app.modules.pipeline.pause_budget import (
    PauseLimits,
    PauseState,
    Verdict,
    pause_verdict,
)
from app.services.llm_gateway import gateway as gateway_module
from app.services.llm_gateway import wire
from app.services.llm_gateway.fleet_breaker import (
    FleetBreaker,
    credential_of,
    provider_pk,
    size_class,
)
from app.services.llm_gateway.fleet_redis import FleetRedis
from app.services.llm_gateway.gateway import (
    LLMGateway,
    ProviderUnavailableError,
    llm_job,
)
from app.services.model_identity.forwarders.litellm_headers import (
    LiteLLMHeadersForwarder,
)
from app.services.model_identity.settings import ConnectionForwardingSettings
from app.services.model_identity.transport.httpx_hook import install_identity_hook

PROVIDER = "Chat:m@host"


class _Wire:
    """A model whose call puts one request per scripted status on the wire through the real request hook, as an SDK
    that retries on its own does; it raises when the last answer is not a 2xx."""

    def __init__(self, *statuses: int) -> None:
        self.statuses = statuses
        self.calls = 0

    async def ainvoke(self, value: object, /, **kwargs: object) -> str:
        self.calls += 1
        for status in self.statuses:
            try:
                wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            except wire.SendAllowanceSpent as exc:
                # What an SDK does with an exception from its transport: it wraps it.
                raise RuntimeError("APIConnectionError") from exc
            wire.note_response(status)
        if self.statuses[-1] >= 400:
            raise RuntimeError(f"status {self.statuses[-1]}")
        return "answer"


@pytest.fixture
def no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(gateway_module.asyncio, "sleep", instant)


async def _serving(fleet: FleetRedis, successes: int) -> FleetBreaker:
    """The key's fleet success record: ``successes`` comparable calls served."""
    breaker = FleetBreaker(fleet)
    pk = provider_pk(PROVIDER, "")
    for _ in range(successes):
        await breaker.admit(pk, size_class(0))
        await breaker.settle(pk, size_class(0), wire.OK)
    return breaker


async def test_every_chargeable_attempt_of_a_call_is_charged(fleet: FleetRedis, no_sleep: None) -> None:
    """20 served calls, then three 429 attempts: the first two are charged (20/20 and 20/21 served), the third is not
    (20/22). The call's error and the run's trace both say 2: the last attempt does not overwrite the earlier ones."""
    await _serving(fleet, 20)
    with llm_job("job-1") as trace, pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2).invoke(_Wire(429), "hi", provider=PROVIDER)
    assert raised.value.charged == 2 and raised.value.chargeable is True
    assert trace.charged == 2
    assert trace.pause_fields()["charged"] == 2


def test_a_pause_adds_every_charged_send_to_the_budget() -> None:
    prior = PauseState(since_ms=0, charged=3)
    decision = pause_verdict(
        prior, sent=True, trial=False, chargeable=True, charged=3, now_ms=1000, ledger_now=0, limits=PauseLimits()
    )
    assert decision.state.charged == 6 and decision.verdict is Verdict.CAPPED
    one = pause_verdict(prior, sent=True, trial=False, chargeable=True, now_ms=1000, ledger_now=0, limits=PauseLimits())
    assert one.state.charged == 4, "an outcome that gives no count is one charged send"


def test_the_charges_stay_with_the_key_they_were_made_on() -> None:
    """A charged failure on key A, then a refusal on key B: the pause is about A, the key that failed the document."""
    with llm_job("job-1") as trace:
        trace.note_attempt("pk-a", "s", "A", chargeable=True, trial=False)
        trace.note_unavailable("pk-a", "s", "A")
        trace.note_unavailable("pk-b", "l", "B")
    fields = trace.pause_fields()
    assert (fields["provider_pk"], fields["size_class"], fields["provider_label"]) == ("pk-a", "s", "A")
    assert fields["charged"] == 1
    with llm_job("job-2") as uncharged:
        uncharged.note_unavailable("pk-a", "s", "A")
        uncharged.note_unavailable("pk-b", "l", "B")
    assert uncharged.pause_fields()["provider_pk"] == "pk-b", "with no charge, the latest key to be unavailable"


async def test_an_sdk_retry_inside_a_served_call_is_a_failed_send(fleet: FleetRedis) -> None:
    """Two 503s the SDK retried, then a 200: the call succeeds and the run has made two failed sends; the served request
    is not one."""
    with llm_job("job-1") as trace:
        assert await LLMGateway(2).invoke(_Wire(503, 503, 200), "hi", provider=PROVIDER) == "answer"
    assert trace.requests == 2


async def test_the_allowance_refuses_a_request_before_it_leaves(fleet: FleetRedis) -> None:
    """With two failed sends left, an SDK retrying a 503 on its own gets its third request refused: the job never goes
    past its absolute cap, whatever one call retries."""
    with llm_job("job-1", allowance=2) as trace, pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2).invoke(_Wire(503, 503, 503, 503), "hi", provider=PROVIDER)
    assert trace.requests == 2
    assert "used the sends" in str(raised.value)
    assert raised.value.sent is True


async def test_a_job_with_no_sends_left_sends_nothing(fleet: FleetRedis) -> None:
    model = _Wire(200)
    with llm_job("job-1", allowance=0) as trace, pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2).invoke(model, "hi", provider=PROVIDER)
    assert model.calls == 0 and trace.requests == 0 and raised.value.sent is False
    with llm_job("job-2", allowance=0):
        pass
    assert await LLMGateway(2).invoke(_Wire(200), "hi", provider=PROVIDER) == "answer", "outside a job, no bound"


async def test_a_call_that_waited_for_a_slot_sees_the_breaker_as_it_is_now(fleet: FleetRedis) -> None:
    """B waits for the only slot while A holds it; the breaker opens meanwhile. B is admitted when it gets the slot, so it
    is refused without a request, instead of sending on an admission taken before the breaker opened."""
    gateway = LLMGateway(1)
    breaker = FleetBreaker(fleet)
    pk, cls = provider_pk(PROVIDER, ""), size_class(0)
    release = asyncio.Event()

    holding = asyncio.Event()

    class Holding:
        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            holding.set()
            await release.wait()
            return "a"

    waiting = _Wire(200)
    a = asyncio.create_task(gateway.invoke(Holding(), "hi", provider=PROVIDER))
    await asyncio.wait_for(holding.wait(), 5.0)  # A was admitted and holds the slot
    b = asyncio.create_task(gateway.invoke(waiting, "hi", provider=PROVIDER))
    await asyncio.sleep(0.05)
    for _ in range(5):
        await breaker.admit(pk, cls)
        await breaker.settle(pk, cls, wire.FAIL)
    release.set()
    assert await a == "a"
    with pytest.raises(ProviderUnavailableError) as refused:
        await b
    assert refused.value.sent is False and waiting.calls == 0


def _forwarding(service_key: str) -> ConnectionForwardingSettings:
    return ConnectionForwardingSettings(enabled=True, service_auth="apiKey", service_api_key=service_key)


class _ProxiedModel:
    """A chat model behind a gateway proxy: its own key, and the httpx client the identity hook is installed on."""

    def __init__(self, api_key: str, service_key: str | None) -> None:
        self.api_key = api_key
        self.client = httpx.AsyncClient()
        if service_key is not None:
            install_identity_hook(self.client, _forwarding(service_key), LiteLLMHeadersForwarder())


def test_the_breaker_key_follows_the_service_key_the_hook_sends() -> None:
    """Background requests carry the connection's service key in place of the model's (identity forwarding): that key
    decides which breaker a call shares."""
    same_sdk_key = credential_of(_ProxiedModel("sk-shared", "svc-1")), credential_of(_ProxiedModel("sk-shared", "svc-2"))
    assert same_sdk_key[0] != same_sdk_key[1], "two service keys are two quotas"
    same_service_key = credential_of(_ProxiedModel("sk-1", "svc-1")), credential_of(_ProxiedModel("sk-2", "svc-1"))
    assert same_service_key[0] == same_service_key[1], "one service key on the wire is one quota"
    assert credential_of(_ProxiedModel("sk-1", None)) != credential_of(_ProxiedModel("sk-2", None))


async def test_each_request_an_sdk_retried_is_settled_and_charged_on_its_own(fleet: FleetRedis) -> None:
    """20 served calls, then one call whose SDK sends three 503s (the gateway does not retry a 503, so this is one
    attempt): each request is settled on its own, so the first two are charged (20/20 and 20/21 served) and the third is
    not (20/22). One settlement for the whole call would charge 1."""
    await _serving(fleet, 20)
    with llm_job("job-1") as trace, pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2).invoke(_Wire(503, 503, 503), "hi", provider=PROVIDER)
    assert trace.charged == 2 and raised.value.charged == 2
    assert trace.requests == 3


async def test_a_cancelled_attempt_counts_its_failed_sends(fleet: FleetRedis) -> None:
    """The stage's deadline cuts off a call whose SDK has already sent and been refused: those sends still count."""

    refused = asyncio.Event()

    class Backing:
        async def ainvoke(self, value: object, /, **kwargs: object) -> str:
            wire._request_hook(httpx.Request("POST", "http://host/v1/chat"))
            wire.note_response(429)
            refused.set()
            await asyncio.sleep(10)  # the SDK's backoff before its retry
            return "never"

    with llm_job("job-1") as trace:
        task = asyncio.create_task(LLMGateway(2).invoke(Backing(), "hi", provider=PROVIDER))
        try:
            await asyncio.wait_for(refused.wait(), 5.0)
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
    assert trace.requests == 1


def test_remote_usage_charges_each_key_its_own_share() -> None:
    """A remote request whose failures fell on two size classes reports each key's share; the caller adds each to that
    key, not the total to one."""
    with llm_job("remote") as remote:
        remote.note_attempt("pk-1", "s", "P", chargeable=True, trial=False)
        remote.note_attempt("pk-1", "l", "P", chargeable=True, trial=False)
        remote.note_attempt("pk-1", "l", "P", chargeable=True, trial=False)
        remote.note_unavailable("pk-1", "l", "P")
    with llm_job("caller") as caller:
        caller.note_remote(remote.usage())
    assert caller.tallies[("pk-1", "s")].charged == 1
    assert caller.tallies[("pk-1", "l")].charged == 2
    assert caller.unavailable == 1



def test_the_input_estimate_counts_tool_call_arguments() -> None:
    """An assistant turn with empty content and large tool-call arguments sends those arguments: it is a large call."""
    from types import SimpleNamespace

    from app.services.llm_gateway.gateway import estimate_tokens

    turn = SimpleNamespace(content="", tool_calls=[{"name": "search", "args": {"q": "x" * 12_000}}])
    assert estimate_tokens([turn]) >= 3_000
    assert estimate_tokens([{"type": "text", "text": "", "tool_calls": [{"args": "y" * 12_000}]}]) >= 3_000


@pytest.mark.parametrize(("allowance", "charged"), [(1, 1), (2, 2)])
async def test_a_request_refused_by_the_allowance_leaves_the_charges_of_those_it_followed(
    fleet: FleetRedis, allowance: int, charged: int
) -> None:
    """20 served calls and an SDK that retries a 503: the allowance refuses the request after the last one it has left. The
    requests that did go out failed and are chargeable (the key was serving others), so the error and the run's trace say so
    instead of reporting sends with no charge."""
    await _serving(fleet, 20)
    with llm_job("job-1", allowance=allowance) as trace, pytest.raises(ProviderUnavailableError) as raised:
        await LLMGateway(2).invoke(_Wire(503, 503, 503, 503), "hi", provider=PROVIDER)
    assert trace.requests == allowance
    assert raised.value.charged == charged and raised.value.chargeable is True
    assert trace.charged == charged
    assert trace.pause_fields()["charged"] == charged
