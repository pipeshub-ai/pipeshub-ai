"""P4.7 MP-1, MP-2: the policy is the pinned routes; failures map to triggers, refusals never do."""

from __future__ import annotations

from typing import Any

import pytest

from harness_core.budget.errors import BudgetExhausted
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.durable.ports import ModelFailed
from harness_core.models.policy import ModelPolicy, ModelPolicyConfig, classify_failure
from harness_core.testing.model_types import ModelCallError


def _policy(*routes: dict[str, Any], **config: Any) -> ModelPolicy:  # noqa: ANN401
    spec = validate_contract(SpecModelPolicy, {"policy": list(routes), "promptVersion": "p"})
    return ModelPolicy.from_spec(spec, ModelPolicyConfig(**config))


def test_mp1_routes_in_pinned_order_and_targets_only_for_their_triggers() -> None:
    policy = _policy(
        {"provider": "a", "model": "a-1", "on": ["5xx"]},
        {"provider": "b", "model": "b-1", "on": ["content_filter"]},
        {"provider": "c", "model": "c-1", "on": ["5xx", "timeout"]},
    )
    assert [r.provider for r in policy.routes] == ["a", "b", "c"]
    assert policy.primary.on == frozenset(), "the primary is never a fallback target"
    assert policy.next_route(policy.primary, "5xx") == policy.routes[2]
    assert policy.next_route(policy.primary, "content_filter") == policy.routes[1]
    assert policy.next_route(policy.routes[1], "content_filter") is None
    assert policy.next_route(policy.primary, "429_exhausted") is None
    assert policy.routes[1].route_id == "b:b-1"


def test_mp1_a_route_without_on_is_never_a_target() -> None:
    policy = _policy({"provider": "a", "model": "a-1"}, {"provider": "b", "model": "b-1"})
    assert all(policy.next_route(policy.primary, t) is None for t in ("5xx", "timeout", "content_filter", "429_exhausted"))


def test_mp1_journaled_route_lookup_and_pin_check() -> None:
    policy = _policy({"provider": "a", "model": "a-1"}, {"provider": "b", "model": "b-1", "on": ["5xx"]})
    assert policy.routed_to("b", "b-1") == policy.routes[1]
    assert policy.routed_to("z", "z-1") == policy.primary
    assert policy.pinned(policy.routes[1])
    assert [policy.retry_delay_s(n) for n in (1, 2, 3)] == [1.0, 2.0, 4.0]


def _failed(error_type: str, cause: BaseException | None = None) -> ModelFailed:
    failed = ModelFailed(error_type)
    failed.__cause__ = cause
    return failed


@pytest.mark.parametrize(
    ("exc", "trigger"),
    [
        (_failed("server_error"), "5xx"),
        (_failed("timeout"), "timeout"),
        (_failed("content_filter"), "content_filter"),
        (_failed("unknown", TransportError("Anthropic overloaded", status_code=529)), "5xx"),
        (_failed("unknown", TransportError("bad gateway", status_code=502)), "5xx"),
        (_failed("unknown", TransportError("request timeout", status_code=408)), "timeout"),
        (_failed("unknown", TimeoutError()), "timeout"),
        (_failed("unknown", TransportError("Error code: 400 - content_filter triggered", status_code=400)), "content_filter"),
        (ModelCallError("server_error", "x", status=503, retry_after_s=None, retryable=True), "5xx"),
        (_failed("budget_exhausted", BudgetExhausted("spent")), None),
        (_failed("auth_error", TransportError("401 unauthorized", status_code=401)), None),
        (_failed("invalid_request", TransportError("Error code: 400", status_code=400)), None),
        (_failed("malformed"), None),
    ],
)
def test_mp2_failures_map_to_triggers_and_refusals_never_do(exc: BaseException, trigger: str | None) -> None:
    assert classify_failure(exc) == trigger
