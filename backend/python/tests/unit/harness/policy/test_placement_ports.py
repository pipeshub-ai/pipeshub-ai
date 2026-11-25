"""OWD-12 and MG-15: where a process runs and where a model call may be served are extension points (``RegionPort``,
``ModelPlacementPort``, ``RunModelPlacement``) read through ``harness_edition()``. This build's defaults run every process
in ``default``, allow every model call, and hand a run's policy out as it is.

RSP-1..RSP-7.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast, override
from unittest.mock import MagicMock

import pytest

from app.harness.access.policies import policy_from_entry
from app.harness.placement import deployment_region, model_regions
from app.harness.run_policy.ports import NO_RUN_MODEL_PLACEMENT, RunModelPlacement
from app.utils import model_placement as placement_module
from app.utils.model_placement import (
    ALLOW_EVERY_MODEL,
    ModelPlacementPort,
    ModelVerdicts,
    Verdict,
    model_placement,
)
from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.guard import GuardedModelAccess
from harness_core.access.memory import InMemoryOrgAccessPolicies
from harness_core.access.types import ENGINE_MODEL_ID, ModelApi
from harness_core.access.vendor_rules import OrgAccessPolicy
from harness_core.edition import HarnessEdition
from harness_core.models.placement import NO_MODEL_REGIONS, ModelRegions, NoModelRegions
from harness_core.region import SINGLE_REGION, RegionPort
from harness_core.testing.model_gateway import GatewayRig, body_of
from harness_engines.vendor_rules import release_vendor_rules
from tests.support.harness_edition import default_harness_edition  # noqa: F401 - the fixture, used by name

pytestmark = pytest.mark.usefixtures("default_harness_edition")

if TYPE_CHECKING:
    from collections.abc import Mapping

PLACEMENT_UNDER_TEST = True


class _Refusing:
    async def verdict(
        self, provider: str, config: Mapping[str, Any], model_name: str | None = None, *, org_id: str | None = None
    ) -> Verdict:
        return Verdict("us-east-1", "the region is not allowed")


class _RefusingPort(ModelPlacementPort):
    @override
    def reader(self, config_service: Any) -> ModelVerdicts:
        return _Refusing()


class _Wrapped:
    def __init__(self, inner: object) -> None:
        self.inner = inner


class _Wrapping(RunModelPlacement):
    @override
    def wrap(self, policy: Any, run: Any, services: Any, live: Any, log: Any) -> Any:
        return _Wrapped(policy)


def _edition(monkeypatch: pytest.MonkeyPatch, ports: Mapping[type[object], object]) -> None:
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: HarnessEdition(ports=dict(ports)))


def test_rsp1_a_process_runs_in_default_and_an_edition_may_name_its_region(monkeypatch: pytest.MonkeyPatch) -> None:
    assert SINGLE_REGION.region({"PIPESHUB_REGION": "eu-west"}) == "default"
    assert deployment_region() == "default"

    class _Placed(RegionPort):
        @override
        def region(self, env: Mapping[str, str]) -> str:
            return "eu-west"

    _edition(monkeypatch, {RegionPort: _Placed()})
    assert deployment_region() == "eu-west"


async def test_rsp2_every_model_call_is_allowed_and_its_region_is_not_known() -> None:
    verdict = await model_placement(MagicMock()).verdict("openAI", {"configuration": {"model": "gpt-5"}})
    assert verdict == Verdict(None, None)
    assert await ALLOW_EVERY_MODEL.reader(None).verdict("bedrock", {}, "m", org_id="org-1") == Verdict(None, None)


async def test_rsp3_an_editions_verdicts_are_the_ones_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    _edition(monkeypatch, {ModelPlacementPort: _RefusingPort()})
    verdict = await placement_module.model_placement(MagicMock()).verdict("openAI", {})
    assert verdict.refusal == "the region is not allowed"


def test_rsp4_a_runs_policy_is_handed_out_as_it_is() -> None:
    policy = object()
    assert NO_RUN_MODEL_PLACEMENT.wrap(policy, MagicMock(), MagicMock(), None, MagicMock()) is policy  # type: ignore[arg-type]


def test_rsp5_run_policies_hand_every_policy_through_the_editions_placement(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.harness.run_policy.select import RunPolicies

    inner = object()
    monkeypatch.setattr(RunPolicies, "_policy", lambda self, run, log: inner)
    deployment = MagicMock()
    assert RunPolicies(MagicMock(), deployment=deployment).for_run(MagicMock(), MagicMock()) is inner
    _edition(monkeypatch, {RunModelPlacement: _Wrapping()})
    wrapped = RunPolicies(MagicMock(), deployment=deployment).for_run(MagicMock(), MagicMock())
    assert isinstance(wrapped, _Wrapped)
    assert wrapped.inner is inner


def test_rsp6_no_route_has_a_region_and_no_pin_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    assert model_regions() is NO_MODEL_REGIONS
    assert NO_MODEL_REGIONS.region_of("bedrock", {"region": "eu-west-1"}, "m", deployment="eu-west") is None
    assert NO_MODEL_REGIONS.pin(["eu"]) == frozenset()
    assert NO_MODEL_REGIONS.pin("not a pin") == frozenset()
    assert NO_MODEL_REGIONS.refusal([frozenset({"eu"})], None, declared="us-east-1") is None
    assert NO_MODEL_REGIONS.pin_of({"editionPin": ["eu"]}) == frozenset()
    assert policy_from_entry({"disabledRules": [], "consumerPlans": False}).region_pins == ()
    # A member no port names is not this build's: the narrowing is unreadable, never read as "nothing narrowed".
    with pytest.raises(AccessPolicyUnavailable):
        policy_from_entry({"disabledRules": [], "consumerPlans": False, "editionPin": ["eu"]})

    class _Pinned(NoModelRegions):
        pin_fields = frozenset({"editionPin"})

        @override
        def pin(self, raw: object) -> frozenset[str]:
            return frozenset(cast("list[str]", raw))

    _edition(monkeypatch, {ModelRegions: _Pinned()})
    assert policy_from_entry({"disabledRules": [], "consumerPlans": False, "editionPin": ["eu"]}).region_pins == (
        frozenset({"eu"}),
    )


async def test_rsp7_the_gateway_asks_the_regions_it_is_given_and_by_default_refuses_nothing() -> None:
    asked: list[tuple[str | None, str | None]] = []

    class _Recording(NoModelRegions):
        @override
        def refusal(self, pins: object, region: str | None, *, declared: str | None = None) -> str | None:
            asked.append((region, declared))
            return None

    async def declared_region(tenant: str, model: str, provider: str) -> str | None:
        return "eu-west-1"

    for regions in (NO_MODEL_REGIONS, _Recording()):
        policies = InMemoryOrgAccessPolicies()
        policies.set("org-1", OrgAccessPolicy(region_pins=(frozenset({"eu"}),)))
        rig = GatewayRig(
            wrap_resolver=lambda inner, regions=regions, policies=policies: GuardedModelAccess(
                inner,
                rules=release_vendor_rules,
                policies=policies,
                offering="oss",
                engine_apis={"opencode": frozenset({ModelApi.CHAT_COMPLETIONS})},
                declared_region=declared_region,
                regions=regions,
            )
        )
        binding = rig.pin(region=None)
        _ = await rig.gateway.chat(
            rig.token(binding), {}, body_of({"model": ENGINE_MODEL_ID, "messages": [{"role": "user", "content": "hi"}]})
        )
        assert len(rig.transport.calls) == 1
    assert asked == [(None, "eu-west-1")]
