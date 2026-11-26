"""E3 MG-1..MG-23 and P6.5 GW-*: the model gateway core, in process, over in-memory ports
and a recording transport."""

from __future__ import annotations

import asyncio
import logging
import math
import subprocess
import sys
from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.access.errors import (
    AccountNotPermitted,
    BudgetExhausted,
    BudgetInsufficient,
    GrantUnavailable,
    ProviderUnsupported,
    RouteNotConfigured,
)
from harness_core.access.ports import ResolvedRoute, SpendKey
from harness_core.access.spend import OutputAllowance, cache_read_weight, usage_spend
from harness_core.access.types import (
    ENGINE_MODEL_ID,
    ROLE_MODEL_IDS,
    ModelApi,
    ModelNeed,
    PinnedRoute,
    RunBudget,
    Spend,
    route_digest,
)
from harness_core.budget.errors import BudgetUnavailable
from harness_core.budget.types import (
    ZERO,
    Amount,
    LedgerUsage,
    Remaining,
    Reservation,
    RunAccountRef,
    Settlement,
)
from harness_core.gateway.cut_calls import DEFAULT_HOLD_TTL_S
from harness_core.gateway.errors import (
    BindingMismatch,
    CallerCredentialHeader,
    ForbiddenCredential,
    GatewayRefusal,
    ModelNotGranted,
    TokenRefused,
)
from harness_core.gateway.model_gateway import (
    ENGINE_TRACKING_HEADERS,
    ChatCompletion,
    ChatStream,
    ProviderCallFailed,
)
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.messages import AssistantMessage, ToolCall, UserMessage
from harness_core.kernel.core.responses import ModelResponse, StopReason, TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.testing.model_gateway import (
    ROUTE,
    GatewayRig,
    RecordedTransport,
    body_of,
    reply_events,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.budget.memory import InMemoryBudgetLedger
    from harness_core.gateway.types import RunAttemptBinding

ANTHROPIC_KEY = "sk-ant-api03-" + "A" * 40
OPENAI_KEY = "sk-proj-" + "B" * 40
GOOGLE_KEY = "AIza" + "C" * 35


def body(**extra: object) -> dict[str, Any]:
    return {"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}], **extra}


async def chat(rig: GatewayRig, token: str | None, payload: object, headers: dict[str, str] | None = None) -> ChatCompletion | ChatStream:
    return await rig.gateway.chat(token, headers or {}, body_of(payload))


async def drain(stream: ChatStream) -> list[dict[str, Any]]:
    return [chunk async for chunk in stream.chunks]


def account(run_id: str = "run-1") -> RunAccountRef:
    return RunAccountRef("org-1", run_id)


async def spent(rig: GatewayRig, run_id: str = "run-1") -> Amount:
    return (await rig.ledger.remaining(account(run_id))).committed


async def prespend(rig: GatewayRig, amount: Amount, run_id: str = "run-1") -> None:
    held = await rig.ledger.reserve(account(run_id), f"earlier-{amount}", amount, minimum=ZERO, ttl_s=60)
    await rig.ledger.commit(held, amount)


@pytest.fixture
def rig() -> GatewayRig:
    return GatewayRig()


async def test_mg1_non_streaming_answer_under_the_neutral_id(rig: GatewayRig) -> None:
    binding = rig.pin()
    tools = [{"type": "function", "function": {"name": "write", "parameters": {"type": "object"}}}]
    outcome = await chat(rig, rig.token(binding), body(tools=tools, messages=[
        {"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]))
    assert isinstance(outcome, ChatCompletion)
    assert outcome.body["model"] == ENGINE_MODEL_ID
    assert outcome.body["choices"][0]["message"]["content"] == "hello"  # type: ignore[index]
    call = rig.transport.calls[0]
    assert call.system == "be brief" and not call.stream
    assert call.messages == [UserMessage(content="hi")]
    assert call.tools is not None and call.tools[0].name == "write"


async def test_mg2_streaming_with_usage(rig: GatewayRig) -> None:
    rig.transport.events = [TextDeltaEvent(delta="hel"), TextDeltaEvent(delta="lo"), reply_events("hello")]
    binding = rig.pin()
    outcome = await chat(rig, rig.token(binding), body(stream=True, stream_options={"include_usage": True}))
    assert isinstance(outcome, ChatStream)
    chunks = await drain(outcome)
    assert "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c["choices"]) == "hello"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["completion_tokens"] == 5
    assert rig.transport.calls[0].stream


async def test_mg3_models_lists_exactly_the_grant(rig: GatewayRig) -> None:
    fallback = PinnedRoute("anthropic", "claude-x", config_key="k2")
    binding = rig.pin(routes=(ROUTE, fallback))
    listed = await rig.gateway.models(rig.token(binding), {})
    assert [m["id"] for m in listed["data"]] == list(ROLE_MODEL_IDS.values())  # type: ignore[index, union-attr]


@pytest.mark.parametrize(
    ("make", "reason"),
    [
        (lambda rig, b: None, "token_missing"),
        (lambda rig, b: "not.a.token", "token_malformed"),
        (lambda rig, b: rig.token(b, issued_at=0, ttl_s=60), "token_expired"),
        (lambda rig, b: rig.token(b, header={"kid": "hgk_unknown"}), "token_unknown_key"),
        (lambda rig, b: rig.token(b, claims={"aud": "pipeshub-agent-harness/attempt"}), "token_audience"),
    ],
)
async def test_mg4_bad_tokens_are_refused_before_anything(
    rig: GatewayRig, make: Callable[[GatewayRig, RunAttemptBinding], str | None], reason: str
) -> None:
    binding = rig.pin()
    with pytest.raises(TokenRefused) as refused:
        await chat(rig, make(rig, binding), body())
    assert refused.value.reason_code == reason
    assert rig.transport.calls == [] and rig.grants.asked == []


# The claims Node minted for a stage-1 request before S1R retired it (SR-8).
STAGE1_REQUEST_CLAIMS: dict[str, object] = {
    "bnd": "request",
    "userId": "user-1",
    "requestId": "req-1",
    "runId": None,
    "parentRunId": None,
    "attemptId": None,
    "fence": None,
    "toolIdsDigest": None,
    "routesDigest": None,
}


async def test_mg5_sr_8_a_stage_1_request_token_is_malformed_and_reaches_nothing() -> None:
    rig = GatewayRig()
    binding = rig.pin()
    token = rig.mint.mint(binding, claims=STAGE1_REQUEST_CLAIMS)
    with pytest.raises(TokenRefused) as refused:
        await chat(rig, token, body())
    assert refused.value.reason_code == "token_malformed"
    assert rig.grants.asked == [] and rig.transport.calls == []


async def test_mg6_a_token_for_other_routes_is_refused(rig: GatewayRig) -> None:
    binding = rig.pin()
    stale = replace(binding, routes_digest=route_digest(()))
    with pytest.raises(BindingMismatch):
        await chat(rig, rig.token(stale), body())
    assert rig.transport.calls == []


async def test_mg7_each_token_reaches_only_its_own_runs_grant(rig: GatewayRig) -> None:
    other_route = PinnedRoute("openai", "gpt-y", config_key="k-other", config_model="gpt-y")
    other_transport = RecordedTransport([reply_events("from run 2")])
    first = rig.pin("run-1")
    second = rig.pin("run-2", routes=(other_route,), transport=other_transport)
    one = await chat(rig, rig.token(first), body())
    two = await chat(rig, rig.token(second), body())
    assert isinstance(one, ChatCompletion) and isinstance(two, ChatCompletion)
    assert two.body["choices"][0]["message"]["content"] == "from run 2"  # type: ignore[index]
    assert rig.grants.asked == ["run-1", "run-2"]
    assert len(rig.transport.calls) == 1 and len(other_transport.calls) == 1
    # A token naming run-2's routes but run-1's id is not run-2's grant.
    forged = replace(first, routes_digest=second.routes_digest)
    with pytest.raises(BindingMismatch):
        await chat(rig, rig.token(forged), body())


async def test_mg8_no_grant_no_call(rig: GatewayRig) -> None:
    binding = rig.pin()
    rig.grants.revoke("org-1", "run-1")
    with pytest.raises(GrantUnavailable):
        await chat(rig, rig.token(binding), body())
    assert rig.transport.calls == []


@pytest.mark.parametrize("model", ["gpt-4o", "gpt-x", "claude-sonnet-4-6", "engine-model-2", ""])
async def test_mg9_a_model_outside_the_grant_is_refused(rig: GatewayRig, model: str) -> None:
    binding = rig.pin()
    with pytest.raises(GatewayRefusal) as refused:
        await chat(rig, rig.token(binding), body(model=model))
    assert refused.value.reason_code == ("model_not_granted" if model else "invalid_chat_request")
    assert rig.transport.calls == []


@pytest.mark.parametrize(
    ("headers", "payload_extra"),
    [
        ({"x-custom": ANTHROPIC_KEY}, {}),
        ({"openai-organization": OPENAI_KEY}, {}),
        ({"x-title": GOOGLE_KEY}, {}),
    ],
)
async def test_mg10_vendor_keys_are_refused(
    rig: GatewayRig, headers: dict[str, str], payload_extra: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()
    caplog.set_level(logging.WARNING)
    with pytest.raises(ForbiddenCredential) as refused:
        await chat(rig, rig.token(binding), body(**payload_extra), headers)
    assert refused.value.http_status == 403
    assert rig.transport.calls == []
    text = caplog.text
    assert "forbidden_credential" in text
    for secret in (ANTHROPIC_KEY, OPENAI_KEY, GOOGLE_KEY):
        assert secret not in text


@pytest.mark.parametrize(
    "payload_extra",
    [{"messages": [{"role": "user", "content": f"use {OPENAI_KEY}"}]}, {"user": ANTHROPIC_KEY}],
)
async def test_mg10_wp103_a_vendor_key_in_the_body_is_hidden_and_the_call_goes_on(
    rig: GatewayRig, payload_extra: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """WP-103: the body is the run's own context going to the org's model; the key never reaches the provider."""
    binding = rig.pin()
    caplog.set_level(logging.WARNING)
    _ = await chat(rig, rig.token(binding), body(**payload_extra))
    assert len(rig.transport.calls) == 1
    sent = repr(rig.transport.calls)
    assert OPENAI_KEY not in sent and ANTHROPIC_KEY not in sent
    assert "model_gateway.secret_hidden" in caplog.text and "forbidden_credential" not in caplog.text
    await rig.refusals.drain()
    hidden = rig.refusal_sink.hidden_secrets  # type: ignore[attr-defined]
    assert [h.gateway for _, h in hidden] == ["model"] and hidden[0][1].kind in ("openai_key", "anthropic_key")


@pytest.mark.parametrize("header", ["api-key", "x-api-key", "X-Goog-Api-Key", "anthropic-api-key"])
async def test_mg11_provider_credential_headers_are_refused(rig: GatewayRig, header: str) -> None:
    binding = rig.pin()
    with pytest.raises(CallerCredentialHeader):
        await chat(rig, rig.token(binding), body(), {header: "0123456789abcdef"})
    assert rig.transport.calls == []


async def test_mg12_tracking_headers_do_not_stop_a_call(rig: GatewayRig) -> None:
    binding = rig.pin()
    headers = {name.replace("*", "lang"): "value-1" for name in ENGINE_TRACKING_HEADERS}
    outcome = await chat(rig, rig.token(binding), body(), headers)
    assert isinstance(outcome, ChatCompletion)


@pytest.mark.parametrize(
    "refusal",
    [RouteNotConfigured("x"), AccountNotPermitted("x"), ProviderUnsupported("x")],
)
async def test_mg13_resolver_refusals_pass_through_without_fallback(
    rig: GatewayRig, refusal: Exception, caplog: pytest.LogCaptureFixture
) -> None:
    binding = rig.pin()

    async def refuse(
        tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: object = None
    ) -> ResolvedRoute:
        raise refusal

    rig.gateway._resolver = type("R", (), {"resolve": staticmethod(refuse)})()
    caplog.set_level(logging.WARNING)
    with pytest.raises(type(refusal)):
        await chat(rig, rig.token(binding), body())
    assert caplog.text.count("model_gateway.refused") == 1
    assert rig.transport.calls == []


async def test_ma4_the_route_is_resolved_for_the_tokens_run(rig: GatewayRig) -> None:
    """P2.4a MA-4: the route's credential is bound to the run the token names."""
    binding = rig.pin()
    inner = rig.gateway._resolver
    runs: list[str | None] = []

    async def spy(
        tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: object = None
    ) -> ResolvedRoute:
        runs.append(run_id)
        return await inner.resolve(tenant, route, run_id=run_id)

    rig.gateway._resolver = type("R", (), {"resolve": staticmethod(spy)})()
    _ = await chat(rig, rig.token(binding), body())
    assert runs == [binding.run_id]


async def test_ad11_the_resolver_is_told_who_calls_from_which_engine_on_which_api(rig: GatewayRig) -> None:
    """A1: the grant's actor and engine and the wire's API reach the resolver (the guard checks them)."""
    binding = rig.pin(actor="user-7", engine="codex")
    inner = rig.gateway._resolver
    needs: list[ModelNeed | None] = []

    async def spy(
        tenant: str, route: PinnedRoute, *, run_id: str | None = None, need: ModelNeed | None = None
    ) -> ResolvedRoute:
        needs.append(need)
        return await inner.resolve(tenant, route, run_id=run_id)

    rig.gateway._resolver = type("R", (), {"resolve": staticmethod(spy)})()
    _ = await chat(rig, rig.token(binding), body())
    assert needs == [ModelNeed("user-7", "codex", ModelApi.CHAT_COMPLETIONS)]


async def test_mg14_an_exhausted_budget_refuses_before_the_call(rig: GatewayRig) -> None:
    binding = rig.pin(budget=RunBudget(max_cost_usd=1.0, max_tokens=None))
    await prespend(rig, Amount(1_000_000, 10))
    with pytest.raises(BudgetExhausted) as refused:
        await chat(rig, rig.token(binding), body())
    assert refused.value.http_status == 402
    assert rig.transport.calls == []


@pytest.mark.parametrize(
    ("budget", "earlier"),
    [
        (RunBudget(max_cost_usd=0.001, max_tokens=None), Amount(990, 0)),
        (RunBudget(max_cost_usd=None, max_tokens=1_000), Amount(0, 995)),
    ],
)
async def test_mg15_a_call_whose_input_alone_cannot_be_paid_is_refused(
    rig: GatewayRig, budget: RunBudget, earlier: Amount
) -> None:
    binding = rig.pin(budget=budget)
    await prespend(rig, earlier)
    with pytest.raises(BudgetInsufficient):
        await chat(rig, rig.token(binding), body(messages=[{"role": "user", "content": "x" * 400}]))
    assert rig.transport.calls == []


async def test_mg16_the_call_that_crosses_stops_the_run(rig: GatewayRig, caplog: pytest.LogCaptureFixture) -> None:
    usage = TokenUsage(input_tokens=1_000, output_tokens=1_000)
    rig.transport.events = [reply_events("big", usage=usage)]
    binding = rig.pin(budget=RunBudget(max_cost_usd=0.01, max_tokens=None))
    caplog.set_level(logging.INFO)
    first = await chat(rig, rig.token(binding), body())
    assert isinstance(first, ChatCompletion)
    assert (await rig.ledger.remaining(account())).exhausted
    assert caplog.text.count("model_gateway.budget_crossed") == 1
    with pytest.raises(BudgetExhausted):
        await chat(rig, rig.token(binding), body())
    assert len(rig.transport.calls) == 1


@pytest.mark.parametrize(
    ("usage", "micros"),
    [
        # claude-sonnet-4-6: $3 in, $15 out per Mtok; cache read 0.1×, write 1.25× of input.
        (TokenUsage(input_tokens=1_000_000), 3_000_000),
        (TokenUsage(output_tokens=1_000), 15_000),
        (TokenUsage(input_tokens=1, output_tokens=1), 18),
        (TokenUsage(input_tokens=1_000, cache_read_tokens=1_000), 300),
        (TokenUsage(input_tokens=1_000, cache_write_tokens=1_000), 3_750),
        (TokenUsage(input_tokens=344, output_tokens=7, cache_read_tokens=11), 1_108),
    ],
)
def test_mg17_charges_are_usage_times_the_pricing_table(usage: TokenUsage, micros: int) -> None:
    assert usage_spend(usage, "claude-sonnet-4-6", charges_usd=True).usd_micros == micros


async def test_mg17_the_charge_lands_on_the_run(rig: GatewayRig) -> None:
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    await chat(rig, rig.token(binding), body())
    assert await spent(rig) == Amount(450, 110)


async def test_mg18_a_vendor_plan_route_counts_tokens_not_dollars(rig: GatewayRig) -> None:
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin(pays="vendor_plan", budget=RunBudget(max_cost_usd=0.000001, max_tokens=None))
    await chat(rig, rig.token(binding), body())
    await chat(rig, rig.token(binding), body())
    assert await spent(rig) == Amount(0, 220)


async def test_mg19_a_broken_stream_ends_with_an_error_and_is_charged_once(rig: GatewayRig) -> None:
    rig.transport.events = [TextDeltaEvent(delta="partial " * 10), reply_events("never")]
    rig.transport.fail = TransportError("boom", status_code=503)
    rig.transport.fail_after = 1
    binding = rig.pin()
    outcome = await chat(rig, rig.token(binding), body(stream=True))
    assert isinstance(outcome, ChatStream)
    chunks = await drain(outcome)
    assert chunks[-1] == {
        "error": {"message": "The provider is unavailable", "type": "provider_error", "param": None, "code": "provider_unavailable"}
    }
    charged = await spent(rig)
    assert charged.tokens > 0 and charged.usd_micros > 0
    # The call's reservation went back; what stays held is its bound's rest, until the hold's time (WP-66).
    left = await rig.ledger.remaining(account())
    assert left.reserved.usd_micros > 0 and left.estimated == 1
    rig.clock.advance(DEFAULT_HOLD_TTL_S + 1)
    assert (await rig.ledger.remaining(account())).reserved == ZERO


async def test_mg19_a_client_that_goes_away_is_still_charged(rig: GatewayRig) -> None:
    rig.transport.events = [TextDeltaEvent(delta="a"), TextDeltaEvent(delta="b"), reply_events("ab")]
    binding = rig.pin()
    outcome = await chat(rig, rig.token(binding), body(stream=True))
    assert isinstance(outcome, ChatStream)
    stream = outcome.chunks
    await anext(stream)
    await stream.aclose()  # pyright: ignore[reportAttributeAccessIssue]
    await asyncio.sleep(0)
    assert (await spent(rig)).tokens > 0


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (TransportError("Error code: 429 - rate limit", status_code=429), 429, "rate_limited"),
        (TransportError("Error code: 500", status_code=500), 502, "provider_unavailable"),
        (TransportError("This model's maximum context length is 128000 tokens", status_code=400), 400, "context_length_exceeded"),
        (TransportError("invalid_request_error: bad schema", status_code=400), 400, "provider_rejected"),
        (TransportError("Incorrect API key provided: sk-...", status_code=401), 502, "provider_auth_failed"),
        (TransportError("content_filter triggered", status_code=400), 400, "content_filter"),
        (TransportError("insufficient_quota", status_code=429), 402, "provider_quota"),
        (ConnectionError("reset"), 502, "provider_unavailable"),
        # FIX-P3-B1: our own code failing to put the request on the wire is not the provider being unavailable.
        (TypeError('can only concatenate list (not "str") to list'), 400, "request_untranslatable"),
    ],
)
async def test_mg20_provider_failures(rig: GatewayRig, error: Exception, status: int, code: str) -> None:
    rig.transport.fail = error
    binding = rig.pin()
    for streaming in (False, True):
        with pytest.raises(ProviderCallFailed) as failed:
            await chat(rig, rig.token(binding), body(stream=streaming))
        assert (failed.value.status, failed.value.code) == (status, code)
        assert "sk-" not in str(failed.value)


@pytest.mark.parametrize(("pinned", "asked", "sent"), [("high", "low", "high"), (None, "low", "low"), (None, None, None)])
async def test_mg21_the_pinned_effort_wins(rig: GatewayRig, pinned: str | None, asked: str | None, sent: str | None) -> None:
    binding = rig.pin(effort=pinned)
    await chat(rig, rig.token(binding), body(**({"reasoning_effort": asked} if asked else {})))
    assert rig.transport.calls[0].effort == sent


async def test_mg22_refusals_are_logged_once_with_correlation(rig: GatewayRig, caplog: pytest.LogCaptureFixture) -> None:
    binding = rig.pin()
    caplog.set_level(logging.INFO)
    token = rig.token(binding)
    with pytest.raises(ModelNotGranted):
        await chat(rig, token, body(model="gpt-4o", messages=[{"role": "user", "content": "secret prompt"}]),
                   {"x-egress-request-id": "egr-7"})
    refusals = [r for r in caplog.records if "model_gateway.refused" in r.getMessage()]
    assert len(refusals) == 1
    line = refusals[0].getMessage()
    for field in ("reason=model_not_granted", "runId=run-1", "attemptId=run-1-a1", "orgId=org-1", "proxyRequestId=egr-7"):
        assert field in line
    assert "secret prompt" not in caplog.text and token not in caplog.text


async def test_mg_tool_round_trip_through_the_gateway(rig: GatewayRig) -> None:
    rig.transport.events = [
        ToolCallDeltaEvent(index=0, id="call_1", name="write", arguments_delta='{"filePath":"a"}'),
        StreamCompleteEvent(
            response=ModelResponse(
                message=AssistantMessage(tool_calls=[ToolCall(id="call_1", name="write", arguments={"filePath": "a"})]),
                stop_reason=StopReason.TOOL_USE,
            )
        ),
    ]
    binding = rig.pin()
    outcome = await chat(rig, rig.token(binding), body(stream=True))
    assert isinstance(outcome, ChatStream)
    chunks = await drain(outcome)
    tool_chunks = [c for c in chunks if c["choices"] and c["choices"][0]["delta"].get("tool_calls")]
    assert tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]["id"] == "call_1"
    assert chunks[-1]["choices"][0]["finish_reason"] == "tool_calls"


def test_mg23_core_imports_without_app() -> None:
    code = (
        "import sys; import harness_core.gateway.model_gateway, harness_core.gateway.model_server, "
        "harness_core.access.memory, harness_core.access.spend, harness_core.testing.contracts.model_access; "
        "bad = [m for m in sys.modules if m == 'app' or m.startswith('app.')]; print(bad); sys.exit(1 if bad else 0)"
    )
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr



# P6.5 GW: the gateway charges the budget ledger.

# ROUTE prices as claude-sonnet-4-6 ($3 in, $15 out per Mtok), whose maximum output the table does not
# know: the allowance is the default. gpt-5.4 ($2.50, $15) publishes 128,000.
GPT = PinnedRoute("openai", "gpt-5.4", config_key="key-g", config_model="gpt-5.4")


class _Spy:
    """Wraps the rig's ledger and records what the gateway asked, in order."""

    def __init__(self, inner: InMemoryBudgetLedger) -> None:
        self.inner = inner
        self.asked: list[tuple[str, Any]] = []
        self.fail: Exception | None = None

    async def reserve(self, account: RunAccountRef, key: str, requested: Amount, **kw: object) -> Reservation:
        self.asked.append(("reserve", (requested, kw["minimum"], kw["credential"] is not None)))
        if self.fail is not None:
            raise self.fail
        return await self.inner.reserve(account, key, requested, **kw)

    async def commit(self, reservation: Reservation, actual: Amount, **kw: object) -> Settlement:
        self.asked.append(("commit", actual))
        if self.fail is not None:
            raise self.fail
        return await self.inner.commit(reservation, actual, **kw)

    async def release(self, reservation: Reservation, **kw: object) -> Settlement:
        self.asked.append(("release", reservation.granted))
        return await self.inner.release(reservation, **kw)

    async def remaining(self, account: RunAccountRef, **kw: object) -> Remaining:
        return await self.inner.remaining(account, **kw)


def spied(**rig_options: object) -> tuple[GatewayRig, _Spy]:
    rig = GatewayRig(**rig_options)
    spy = _Spy(rig.ledger)
    rig.gateway._budget = spy  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    return rig, spy


@pytest.mark.parametrize(
    ("route", "allowance", "output_tokens"),
    [
        (ROUTE, OutputAllowance(), 32_768),
        # The engine call's own output limit (P4.17), below gpt-6-luna's 128,000; GW-2 has the maximum without one.
        (GPT, OutputAllowance(), 32_768),
        (GPT, OutputAllowance(cap_tokens=8_000), 8_000),
        (ROUTE, OutputAllowance(default_tokens=1_000), 1_000),
    ],
)
async def test_gw1_a_call_reserves_its_input_and_output_allowance_first(
    route: PinnedRoute, allowance: OutputAllowance, output_tokens: int
) -> None:
    rig, spy = spied(allowance=allowance)
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=10, output_tokens=2))]
    binding = rig.pin(routes=(route,), budget=RunBudget(max_cost_usd=100.0, max_tokens=None))
    await chat(rig, rig.token(binding), body())
    kind, (requested, minimum, with_token) = spy.asked[0]
    assert kind == "reserve" and with_token
    # The token minimum is the prompt as if read from the cache (ENGBUDGET).
    prompt = requested.tokens - output_tokens
    assert minimum.tokens == math.ceil(prompt * cache_read_weight(route.config_model or route.model, prompt_tokens=prompt))
    assert 0 < minimum.tokens < prompt
    assert minimum.usd_micros > 0 and requested.usd_micros > minimum.usd_micros
    assert [k for k, _ in spy.asked] == ["reserve", "commit"]
    assert len(rig.transport.calls) == 1


async def test_gw2_the_usage_is_committed_and_the_rest_returns() -> None:
    rig, spy = spied()
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    await chat(rig, rig.token(binding), body())
    assert spy.asked[-1] == ("commit", Amount(450, 110))
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (Amount(450, 110), ZERO)
    assert left.available.usd_micros == 2_000_000 - 450


async def test_ev10_the_ledger_holds_each_calls_provider_usage_as_reported() -> None:
    """EV-10, OWD-8: every call the gateway settles tells the ledger the provider's own counts, cache reads and
    writes included; the run's ``used`` is their sum, the authority a turn's usage is read from."""
    rig, _ = spied()
    rig.transport.events = [
        reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10, cache_read_tokens=60, cache_write_tokens=5))
    ]
    binding = rig.pin()
    await chat(rig, rig.token(binding), body())
    rig.transport.events = [reply_events("y", usage=TokenUsage(input_tokens=30, output_tokens=4))]
    await chat(rig, rig.token(binding), body())
    assert (await rig.ledger.remaining(account())).used == LedgerUsage(130, 14, 60, 5)


async def test_ev10_the_ledger_holds_the_call_when_the_last_chunk_is_yielded(rig: GatewayRig) -> None:
    """OWD-8: an engine ends its turn once its last call's stream ends, and the turn's usage is read from the ledger
    right then; so the gateway settles a call before the caller can see it end."""
    rig.transport.events = [
        TextDeltaEvent(delta="a"), reply_events("a", usage=TokenUsage(input_tokens=40, output_tokens=3))
    ]
    binding = rig.pin()
    outcome = await chat(rig, rig.token(binding), body(stream=True))
    assert isinstance(outcome, ChatStream)
    seen: list[LedgerUsage] = [(await rig.ledger.remaining(account())).used async for _chunk in outcome.chunks]
    assert seen[-1] == LedgerUsage(40, 3)
    assert len([u for u in seen if u == LedgerUsage(40, 3)]) < len(seen), "the call was settled before it began"


async def test_ev10_a_stream_cut_before_its_usage_tells_the_ledger_the_estimate_it_charged(rig: GatewayRig) -> None:
    rig.transport.events = [TextDeltaEvent(delta="partial " * 10), reply_events("never")]
    rig.transport.fail = TransportError("boom", status_code=503)
    rig.transport.fail_after = 1
    binding = rig.pin()
    outcome = await chat(rig, rig.token(binding), body(stream=True))
    assert isinstance(outcome, ChatStream)
    await drain(outcome)
    left = await rig.ledger.remaining(account())
    assert left.used.input_tokens > 0 and left.used.output_tokens > 0
    assert left.used.input_tokens + left.used.output_tokens == left.committed.tokens


@pytest.mark.parametrize("stream", [False, True])
async def test_gw3_a_provider_error_before_any_output_releases(stream: bool) -> None:
    rig, spy = spied()
    rig.transport.fail = TransportError("down", status_code=503)
    binding = rig.pin()
    with pytest.raises(ProviderCallFailed):
        await chat(rig, rig.token(binding), body(stream=stream))
    assert [k for k, _ in spy.asked] == ["reserve", "release"]
    left = await rig.ledger.remaining(account())
    assert (left.committed, left.reserved) == (ZERO, ZERO)


async def test_gw5_concurrent_calls_of_one_run_cannot_overshoot_together() -> None:
    gate = asyncio.Event()

    class Slow(RecordedTransport):
        async def complete(self, *args: object, **kwargs: object) -> ModelResponse:
            await gate.wait()
            return await super().complete(*args, **kwargs)

    # Each call: ~3 input tokens and 1,000 output tokens at gpt-5.4 prices = 15,008 micros;
    # the allowance (1,000 output tokens) covers it, so the cap holds exactly.
    rig = GatewayRig(transport=Slow(), allowance=OutputAllowance(cap_tokens=1_000))
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=3, output_tokens=1_000))]
    binding = rig.pin(routes=(GPT,), budget=RunBudget(max_cost_usd=0.046, max_tokens=None))
    calls = [asyncio.create_task(chat(rig, rig.token(binding), body())) for _ in range(8)]
    await asyncio.sleep(0)
    for _ in range(20):
        await asyncio.sleep(0)
    held = (await rig.ledger.remaining(account())).reserved.usd_micros
    gate.set()
    outcomes = await asyncio.gather(*calls, return_exceptions=True)
    served = [o for o in outcomes if isinstance(o, ChatCompletion)]
    refused = [o for o in outcomes if isinstance(o, BudgetInsufficient | BudgetExhausted)]
    assert len(served) + len(refused) == 8
    assert 3 <= len(served) < 8
    assert held <= 46_000
    assert (await spent(rig)).usd_micros <= 46_000 + 15_008  # at most one partial grant's uncovered part


async def test_gw6_the_crossing_call_is_logged_once_then_the_run_is_exhausted(caplog: pytest.LogCaptureFixture) -> None:
    rig = GatewayRig()
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=1_000, output_tokens=1_000))]
    binding = rig.pin(budget=RunBudget(max_cost_usd=0.02, max_tokens=None))
    caplog.set_level(logging.INFO)
    await chat(rig, rig.token(binding), body())
    await chat(rig, rig.token(binding), body())
    assert caplog.text.count("model_gateway.budget_crossed") == 1
    with pytest.raises(BudgetExhausted):
        await chat(rig, rig.token(binding), body())


async def test_gw7_a_vendor_plan_route_reserves_no_dollars() -> None:
    rig, spy = spied()
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin(pays="vendor_plan")
    await chat(rig, rig.token(binding), body())
    _, (requested, minimum, _) = spy.asked[0]
    assert requested.usd_micros == minimum.usd_micros == 0 and requested.tokens > 0


@pytest.mark.parametrize("failure", [ConnectionError("node down"), RuntimeError("bug")])
async def test_gw8_an_unreachable_ledger_refuses_the_call(failure: Exception, caplog: pytest.LogCaptureFixture) -> None:
    rig, spy = spied()
    spy.fail = failure
    binding = rig.pin()
    caplog.set_level(logging.WARNING)
    with pytest.raises(BudgetUnavailable) as refused:
        await chat(rig, rig.token(binding), body())
    assert refused.value.http_status == 503
    assert rig.transport.calls == []
    assert caplog.text.count("model_gateway.refused") == 1


async def test_gw9_a_failed_commit_is_logged_and_the_reservation_expires(caplog: pytest.LogCaptureFixture) -> None:
    rig, spy = spied()
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin()
    caplog.set_level(logging.INFO)

    async def broken(reservation: Reservation, actual: Amount, **kw: object) -> Settlement:
        spy.asked.append(("commit", actual))
        raise ConnectionError("node down")

    spy.commit = broken  # type: ignore[method-assign]
    await chat(rig, rig.token(binding), body())
    assert [k for k, _ in spy.asked] == ["reserve", "commit", "commit", "commit"]
    assert caplog.text.count("model_gateway.charge_failed") == 1
    assert (await rig.ledger.remaining(account())).reserved != ZERO
    rig.clock.advance(901)
    assert (await rig.ledger.remaining(account())).reserved == ZERO


async def test_gw10_a_run_admitted_before_the_ledger_starts_at_its_e3_spend() -> None:
    rig = GatewayRig()
    reads: list[SpendKey] = []
    legacy_spent = rig.legacy.spent

    async def counted(key: SpendKey) -> Spend:
        reads.append(key)
        return await legacy_spent(key)

    rig.legacy.spent = counted  # type: ignore[method-assign]
    await rig.legacy.charge(SpendKey("org-1", "run-1"), "e3-call", Spend(1_500_000, 900))
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin(budget=RunBudget(max_cost_usd=2.0, max_tokens=None))
    await chat(rig, rig.token(binding), body())
    await chat(rig, rig.token(binding), body())
    assert await spent(rig) == Amount(1_500_000 + 900, 900 + 220)
    assert reads == [SpendKey("org-1", "run-1")]


async def test_gw10_a_run_without_e3_spend_opens_from_its_pin_alone() -> None:
    rig = GatewayRig()
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    binding = rig.pin(budget=RunBudget(max_cost_usd=0.5, max_tokens=1_000))
    await chat(rig, rig.token(binding), body())
    left = await rig.ledger.remaining(account())
    assert left.available.usd_micros == 500_000 - 450 and left.available.tokens == 1_000 - 110


async def test_gw11_a_childs_calls_count_against_its_parent_and_the_root() -> None:
    rig = GatewayRig()
    rig.transport.events = [reply_events("x", usage=TokenUsage(input_tokens=100, output_tokens=10))]
    parent = rig.pin("parent", budget=RunBudget(max_cost_usd=0.001, max_tokens=None))
    child = rig.pin("child", parent_run_id="parent", budget=RunBudget(max_cost_usd=0.001, max_tokens=None))
    await chat(rig, rig.token(child), body())
    await chat(rig, rig.token(child), body())
    assert await spent(rig, "parent") == Amount(900, 220)
    with pytest.raises(BudgetInsufficient):
        await chat(rig, rig.token(parent), body(messages=[{"role": "user", "content": "x" * 400}]))
