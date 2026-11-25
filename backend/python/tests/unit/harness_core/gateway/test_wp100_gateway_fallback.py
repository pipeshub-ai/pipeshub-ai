"""WP100-FB-3..10 (G-48, C20.49): an engine's provider fallback goes through the Model Gateway, with the loop's policy.

The kernel moves a loop run whose provider is down to the next route its pin names for that failure (P4.7,
``ModelPolicy``) and journals ``model.fallback``. An engine reaches its models only through the gateway, so the
gateway now does the same with the same object: ``ModelPolicy.from_pinned`` over the run's grant, the same
``classify_failure`` and the same per-route ``on``. A call that failed before any output went to the engine is
placed again on the alternate (its reservation released, a new one taken); the run's later calls go there at once;
the move is reported once to the run's journal, so the chat says the model it moved to. A content filter, a rate
limit and a refusal never move a call; nor does a primary pinned to a person's own key (A1), nor a gateway the host
did not turn the fallback on for.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import pytest

from app.harness.access.grants import parse_grant
from harness_core.access.errors import RouteNotConfigured
from harness_core.access.types import AccountKind, AccountRef, PinnedRoute
from harness_core.budget.types import ZERO, RunAccountRef
from harness_core.gateway.model_gateway import ProviderCallFailed
from harness_core.gateway.refusal_reports import (
    InMemoryRefusalSink,
    ModelFallbackReport,
)
from harness_core.kernel.core.exceptions import TransportError
from harness_core.models.policy import ModelPolicy, classify_failure
from harness_core.testing.model_gateway import (
    GatewayRig,
    RecordedTransport,
    body_of,
    org_key_route,
    reply_events,
)

if TYPE_CHECKING:
    from harness_core.gateway.model_gateway import ChatCompletion, ChatStream

ALTERNATE_ON = frozenset({"5xx", "timeout", "429_exhausted"})
PRIMARY = PinnedRoute("openai", "gpt-x", config_key="key-1", config_model="gpt-x")
ALTERNATE = PinnedRoute("anthropic", "claude-x", config_model="claude-x", by_name=True, on=ALTERNATE_ON)
SECOND = PinnedRoute("azureopenai", "gpt-y", config_model="gpt-y", by_name=True, on=ALTERNATE_ON)


def _body(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {"model": "engine-model", "messages": [{"role": "user", "content": "hi"}], **extra}


class _Rig:
    def __init__(self, *, fallback: bool = True, routes: tuple[PinnedRoute, ...] = (PRIMARY, ALTERNATE)) -> None:
        self.sink = InMemoryRefusalSink()
        self.rig = GatewayRig(refusal_sink=self.sink)
        self.rig.gateway._fallback = fallback  # pyright: ignore[reportPrivateUsage]
        self.primary = RecordedTransport(fail=TransportError("Error code: 503", status_code=503))
        self.alternate = RecordedTransport([reply_events("from the alternate")])
        self.second = RecordedTransport([reply_events("from the second")])
        self.binding = self.rig.pin(routes=routes)
        served = {PRIMARY: self.primary, ALTERNATE: self.alternate, SECOND: self.second}
        for route in routes:
            self.rig.resolver.add("org-1", route, org_key_route(route, served[route]))

    async def call(self, **extra: Any) -> ChatCompletion | ChatStream:  # noqa: ANN401
        return await self.rig.gateway.chat(self.rig.token(self.binding), {}, body_of(_body(**extra)))

    async def reports(self) -> list[ModelFallbackReport]:
        await self.rig.refusals.drain()
        return [report for _, report in self.sink.fallbacks]


def _text(outcome: ChatCompletion | ChatStream) -> str:
    body = outcome.body  # type: ignore[union-attr]
    return body["choices"][0]["message"]["content"]


async def test_wp100_fb_3_a_provider_down_call_is_served_by_the_pinned_alternate() -> None:
    rig = _Rig()
    outcome = await rig.call()
    assert _text(outcome) == "from the alternate"
    assert len(rig.primary.calls) == 1 and len(rig.alternate.calls) == 1
    assert outcome.body["model"] == "engine-model", "the engine keeps naming its neutral id"  # type: ignore[union-attr]
    assert await rig.reports() == [ModelFallbackReport("openai", "gpt-x", "anthropic", "claude-x", "5xx")]
    # The failed call's hold went back: only the alternate's answer is charged.
    left = await rig.rig.ledger.remaining(RunAccountRef(rig.binding.tenant, rig.binding.run_id))
    assert left.reserved == ZERO


async def test_wp100_fb_4_the_runs_later_calls_go_to_the_alternate_and_the_move_is_reported_once() -> None:
    rig = _Rig()
    _ = await rig.call()
    second = await rig.call()
    assert _text(second) == "from the alternate"
    assert len(rig.primary.calls) == 1, "a provider found down is not asked again within the run"
    assert len(rig.alternate.calls) == 2
    assert len(await rig.reports()) == 1


async def test_wp100_fb_5_a_stream_that_fails_opening_moves_too() -> None:
    rig = _Rig()
    rig.primary.fail = httpx.ConnectError("connection refused")
    stream = await rig.call(stream=True)
    chunks = [chunk async for chunk in stream.chunks]  # type: ignore[union-attr]
    assert any("from the alternate" in str(chunk) for chunk in chunks)
    assert [r.reason for r in await rig.reports()] == ["5xx"]


@pytest.mark.parametrize(
    "failure",
    [
        TransportError("Error code: 429 - rate limited", status_code=429),
        TransportError("Error code: 400 - content_filter: the response was filtered", status_code=400),
        TransportError("Error code: 400 - bad request", status_code=400),
    ],
    ids=["rate_limit", "content_filter", "rejected"],
)
async def test_wp100_fb_6_a_limit_a_filter_or_a_rejection_never_moves_a_call(failure: TransportError) -> None:
    rig = _Rig()
    rig.primary.fail = failure
    with pytest.raises(ProviderCallFailed):
        _ = await rig.call()
    assert rig.alternate.calls == [] and await rig.reports() == []


async def test_wp100_fb_7_a_primary_on_a_persons_own_key_never_moves() -> None:
    own = PinnedRoute("openai", "gpt-x", account=AccountRef(AccountKind.USER_API_KEY, "users/user-1/openai"))
    rig = _Rig(routes=(PRIMARY, ALTERNATE))
    # Re-pin the run with the person's key as its first route (A1: it pays for nothing but its own model).
    rig.binding = rig.rig.pin(run_id="run-2", routes=(own, ALTERNATE))
    rig.rig.resolver.add("org-1", own, org_key_route(own, rig.primary, pays="user"))
    rig.rig.resolver.add("org-1", ALTERNATE, org_key_route(ALTERNATE, rig.alternate))
    with pytest.raises(ProviderCallFailed):
        _ = await rig.call()
    assert rig.alternate.calls == []


async def test_wp100_fb_8_an_alternate_the_org_no_longer_configures_is_skipped_for_the_next() -> None:
    rig = _Rig(routes=(PRIMARY, ALTERNATE, SECOND))
    rig.rig.resolver.refuse("org-1", ALTERNATE, RouteNotConfigured("gone"))
    outcome = await rig.call()
    assert _text(outcome) == "from the second"
    assert await rig.reports() == [ModelFallbackReport("openai", "gpt-x", "azureopenai", "gpt-y", "5xx")]


async def test_wp100_fb_8b_no_alternate_left_answers_the_providers_failure() -> None:
    rig = _Rig()
    rig.alternate.fail = TransportError("Error code: 502", status_code=502)
    with pytest.raises(ProviderCallFailed) as raised:
        _ = await rig.call()
    assert raised.value.code == "provider_unavailable"


async def test_wp100_fb_9_a_gateway_without_the_fallback_never_moves() -> None:
    rig = _Rig(fallback=False)
    with pytest.raises(ProviderCallFailed):
        _ = await rig.call()
    assert rig.alternate.calls == [] and await rig.reports() == []


def test_wp100_fb_10_the_grant_carries_each_alternates_triggers_and_the_policy_is_the_kernels() -> None:
    grant = parse_grant(
        {
            "runId": "run-1",
            "routes": [
                {"provider": "openai", "model": "gpt-x", "modelKey": "key-1", "modelName": "gpt-x"},
                {"provider": "anthropic", "model": "claude-x", "on": ["5xx", "timeout", "429_exhausted"]},
            ],
            "budgets": {"maxCostUsd": 1, "maxTokens": 1000},
        },
        tenant="org-1",
    )
    primary, alternate = grant.routes
    assert primary.on == frozenset() and alternate.on == ALTERNATE_ON
    # An alternate names its model as a role's route does: served by the configured model that lists it.
    assert alternate.by_name and alternate.config_model == "claude-x" and alternate.config_key is None
    # Its triggers are not part of its id: the token's routes digest is unchanged.
    assert alternate.route_id == "anthropic:claude-x"
    # Only the engine's own model id is served; the alternate stays unnamed to the engine.
    assert [m.model_id for m in grant.models if m.route is alternate] == []
    policy = ModelPolicy.from_pinned(grant.routes)
    assert policy.next_route(policy.primary, "5xx") == policy.routes[1]
    assert policy.next_route(policy.primary, "content_filter") is None


@pytest.mark.parametrize(
    ("failure", "trigger"),
    [
        (httpx.ConnectError("refused"), "5xx"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (TransportError("Error code: 429", status_code=429), None),
    ],
)
def test_wp100_fb_10b_one_classifier_names_what_moves_a_call(failure: BaseException, trigger: str | None) -> None:
    assert classify_failure(failure) == trigger
