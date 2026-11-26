"""E3 PA-4 and the grants contract on ``NodeRunModelGrants`` against a fake Node (httpx mock transport)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import httpx
import pytest

from app.harness.access.grants import NodeRunModelGrants, parse_grant
from harness_core.access.errors import GrantUnavailable
from harness_core.access.types import (
    AccountKind,
    AccountRef,
    PinnedRoute,
    RunBudget,
    route_digest,
)
from harness_core.gateway.types import GatewayClaims, RunAttemptBinding
from harness_core.testing.contracts.model_access import (
    ROUTE,
    RunModelGrantsContract,
    run_binding,
)


@dataclass
class FakeNode:
    """Answers ``GET …/runs/{runId}/model-grant`` like Node: only the run's own token, only while pinned."""

    pins: dict[str, tuple[tuple[PinnedRoute, ...], RunBudget]] = field(default_factory=dict)
    status: int | None = None
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx.Response(self.status, json={"error": {"code": "AGENT_HARNESS_RUN_NOT_LEASED"}})
        run_id = request.url.path.split("/")[-2]
        if request.headers.get("authorization") != f"Bearer token-{run_id}":
            return httpx.Response(401, json={"error": {"code": "AGENT_HARNESS_ATTEMPT_CLAIMS"}})
        pin = self.pins.get(run_id)
        if pin is None:
            return httpx.Response(404, json={"error": {"code": "HTTP_NOT_FOUND"}})
        routes, budget = pin
        return httpx.Response(
            200,
            json={
                "runId": run_id,
                "attemptId": f"{run_id}-attempt-1",
                "routesDigest": route_digest(routes),
                "routes": [
                    {"provider": r.provider, "model": r.model, "modelKey": r.config_key, "modelName": r.config_model}
                    for r in routes
                ],
                "reasoningEffort": "low",
                "budgets": {"maxCostUsd": budget.max_cost_usd, "maxTokens": budget.max_tokens},
            },
        )


@dataclass
class _Harness:
    node: FakeNode = field(default_factory=FakeNode)
    now: list[float] = field(default_factory=lambda: [0.0])
    grants: NodeRunModelGrants = field(init=False)

    def __post_init__(self) -> None:
        client = httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(self.node.handle))
        self.grants = NodeRunModelGrants(
            base_url="http://node.test", http_client=client, monotonic=lambda: self.now[0], wall=lambda: 1_000.0
        )

    def pin(self, binding: RunAttemptBinding, routes: tuple[PinnedRoute, ...], budget: RunBudget) -> None:
        self.node.pins[binding.run_id] = (routes, budget)

    def token(self, binding: RunAttemptBinding) -> str:
        return f"token-{binding.run_id}"


class TestNodeRunModelGrants(RunModelGrantsContract):
    @pytest.fixture
    def grants_harness(self) -> _Harness:
        return _Harness()


def _claims(binding: RunAttemptBinding, expires_at: int = 1_600) -> GatewayClaims:
    return GatewayClaims(binding, "hgk_1", expires_at)


async def test_pa4_parses_the_grant_and_sends_the_token_only_to_node() -> None:
    harness = _Harness()
    binding = run_binding()
    harness.pin(binding, (ROUTE,), RunBudget(1.5, 1_000))
    grant = await harness.grants.grant(_claims(binding), "token-run-1")
    assert grant.reasoning_effort == "low"
    assert grant.budget == RunBudget(1.5, 1_000)
    request = harness.node.requests[0]
    assert request.url.path == "/api/v1/agent-harness/internal/runs/run-1/model-grant"
    assert request.method == "GET"
    assert "token-run-1" not in repr(harness.grants)


@pytest.mark.parametrize(("status", "http_status"), [(401, 401), (404, 403), (409, 403), (500, 503)])
async def test_pa4_node_refusals_are_grant_unavailable(status: int, http_status: int) -> None:
    harness = _Harness()
    harness.node.status = status
    with pytest.raises(GrantUnavailable) as refused:
        await harness.grants.grant(_claims(run_binding()), "token-run-1")
    assert refused.value.http_status == http_status
    assert refused.value.detail in ("run_not_leased", f"status_{status}")


async def test_pa4_unreachable_node_is_503() -> None:
    def down(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(down))
    with pytest.raises(GrantUnavailable) as refused:
        await NodeRunModelGrants(base_url="http://node.test", http_client=client).grant(
            _claims(run_binding()), "token-run-1"
        )
    assert (refused.value.http_status, refused.value.detail) == (503, "node_unreachable")


async def test_pa4_cached_per_attempt_until_the_ttl_or_the_tokens_expiry(caplog: pytest.LogCaptureFixture) -> None:
    harness = _Harness()
    binding = run_binding()
    harness.pin(binding, (ROUTE,), RunBudget(1.0, 10))
    caplog.set_level(logging.DEBUG)
    await harness.grants.grant(_claims(binding), "token-run-1")
    await harness.grants.grant(_claims(binding), "token-run-1")
    assert len(harness.node.requests) == 1
    harness.now[0] = 61.0
    await harness.grants.grant(_claims(binding), "token-run-1")
    assert len(harness.node.requests) == 2
    # A token expiring in 5 s keeps its grant at most 5 s.
    short = run_binding("run-1")
    harness.now[0] = 200.0
    await harness.grants.grant(_claims(short, expires_at=1_005), "token-run-1")
    harness.now[0] = 206.0
    await harness.grants.grant(_claims(short, expires_at=1_005), "token-run-1")
    assert len(harness.node.requests) == 4
    assert "token-run-1" not in caplog.text


def test_ad11_the_grant_carries_the_pinned_account_the_actor_and_the_engine() -> None:
    """A1: Node's grant (``grantedRoutes``) as the gateway reads it; only a model account may be pinned by reference."""
    grant = parse_grant(
        {
            "runId": "run-1",
            "routes": [
                {
                    "provider": "openai",
                    "model": "gpt-5",
                    "account": {"kind": "user_api_key", "accountId": "users/u1/openAI"},
                    "pays": "user",
                }
            ],
            "budgets": {"maxCostUsd": 1, "maxTokens": 10},
            "actorId": "u1",
            "engine": "opencode",
        },
        tenant="org-1",
    )
    assert grant.routes[0].account == AccountRef(AccountKind.USER_API_KEY, "users/u1/openAI")
    assert grant.routes[0].route_id == "openai:gpt-5@user_api_key:users/u1/openAI"
    assert (grant.actor, grant.engine) == ("u1", "opencode")
    for account in ({"kind": "org_api_key", "accountId": "m-1"}, {"kind": "user_api_key"}, "users/u1/openAI"):
        with pytest.raises(GrantUnavailable) as refused:
            parse_grant(
                {"runId": "r", "routes": [{"provider": "p", "model": "m", "account": account}], "budgets": {}},
                tenant="org-1",
            )
        assert refused.value.detail == "malformed_grant"
    legacy = parse_grant({"runId": "r", "routes": [{"provider": "p", "model": "m"}], "budgets": {}}, tenant="org-1")
    assert (legacy.actor, legacy.engine, legacy.routes[0].account) == (None, None, None)
