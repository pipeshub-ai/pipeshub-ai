"""P4.3/P4.7 FL-2, FL-3: the flags give the durable kernel provider state and the model policy, or nothing new."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from app.harness.assembly import model as model_module
from app.harness.assembly.context import RunContext
from app.harness.assembly.model import ResolvedModel, build_transport
from app.harness.assembly.model_policy import ConfiguredRoutePorts, build_model_policy
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
from harness_core.kernel.durable.routing import RoutedModelPort
from harness_core.models.errors import ModelRouteNotPinned
from harness_core.models.policy import ModelPolicy
from harness_core.testing.model_gateway import RecordedTransport

from . import world as w

PRIMARY = object()


def _model() -> ResolvedModel:
    return ResolvedModel(
        raw_llm=None, provider="openAI", is_multimodal=False, context_length=None, is_reasoning=False,
        port=PRIMARY,  # type: ignore[arg-type]
        transport=RecordedTransport(),
    )


def _spec_model(*routes: dict[str, Any]) -> SpecModelPolicy:
    return validate_contract(SpecModelPolicy, {"policy": list(routes), "promptVersion": "pipeshub-agent-2026-09"})


TWO = (
    {"provider": "openai", "model": "gpt-x"},
    {"provider": "azureopenai", "model": "gpt-y", "on": ["5xx"]},
)


def test_fl2_off_the_kernel_gets_todays_port_and_no_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HARNESS_MODEL_FALLBACK", "false")
    built: list[int] = []

    def ports() -> Any:  # noqa: ANN401
        built.append(1)
        raise AssertionError("never built with the flag off")

    assert build_model_policy(_spec_model(*TWO), _model(), ports) == (PRIMARY, None)
    assert built == []


@pytest.mark.parametrize("value", [None, "true"], ids=["default", "on"])
def test_fl3_on_a_routed_port_over_the_pinned_routes(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    """WP100-FB-2: on by default (``HARNESS_MODEL_FALLBACK``)."""
    if value is None:
        monkeypatch.delenv("HARNESS_MODEL_FALLBACK", raising=False)
    else:
        monkeypatch.setenv("HARNESS_MODEL_FALLBACK", value)
    port, config = build_model_policy(_spec_model(*TWO), _model(), lambda: None)  # type: ignore[arg-type,return-value]
    assert isinstance(port, RoutedModelPort) and config is not None
    assert [r.route_id for r in port.policy.routes] == ["openai:gpt-x", "azureopenai:gpt-y"]
    assert port.active == port.policy.primary


class _Config:
    def __init__(self, document: dict[str, Any]) -> None:
        self.document = document

    async def get_config(self, key: str, **_: Any) -> Any:  # noqa: ANN401
        return self.document


class _Services:
    def __init__(self, document: dict[str, Any]) -> None:
        self._config = _Config(document)

    def config_service(self) -> _Config:
        return self._config


AI_MODELS = {
    "llm": [
        {"provider": "openAI", "modelKey": "model-1", "configuration": {"model": "gpt-x"}},
        {"provider": "azureOpenAI", "modelKey": "model-2", "configuration": {"model": "gpt-z, gpt-y"}},
    ]
}


def _route_ports(document: dict[str, Any], spec_model: SpecModelPolicy) -> tuple[ConfiguredRoutePorts, list[RunContext], list[str]]:
    run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[])))
    resolved: list[RunContext] = []
    metered: list[str] = []

    async def resolve(routed: RunContext, services: Any) -> ResolvedModel:  # noqa: ANN401
        resolved.append(routed)
        return replace(_model(), port=f"port:{routed.model.model_key}")  # type: ignore[arg-type]

    def meter(model: ResolvedModel, routed: RunContext) -> ResolvedModel:
        metered.append(routed.model.model)
        return model

    ports = ConfiguredRoutePorts(
        run, _Services(document), tuple(spec_model.policy), resolve=resolve, meter=meter  # type: ignore[arg-type]
    )
    return ports, resolved, metered


async def test_fl3_a_fallback_route_is_the_orgs_configured_model_reached_and_metered_as_the_primary() -> None:
    spec_model = _spec_model(*TWO)
    ports, resolved, metered = _route_ports(AI_MODELS, spec_model)
    route = ModelPolicy.from_spec(spec_model).routes[1]
    assert await ports.port(route) == "port:model-2"
    (routed,) = resolved
    assert (routed.model.provider, routed.model.model, routed.model.model_key) == ("azureopenai", "gpt-y", "model-2")
    assert metered == ["gpt-y"]


async def test_fl3_a_route_the_org_no_longer_configures_or_one_on_a_model_account_is_refused() -> None:
    spec_model = _spec_model(TWO[0], {"provider": "anthropic", "model": "claude-x", "on": ["5xx"]})
    ports, resolved, _ = _route_ports(AI_MODELS, spec_model)
    with pytest.raises(ModelRouteNotPinned):
        await ports.port(ModelPolicy.from_spec(spec_model).routes[1])
    account = {
        **TWO[1],
        "access": {"route": "gateway", "accountKind": "user_api_key", "accountId": "users/u1/azureopenai", "pays": "user"},
    }
    spec_model = _spec_model(TWO[0], account)
    ports, resolved, _ = _route_ports(AI_MODELS, spec_model)
    with pytest.raises(ModelRouteNotPinned):
        await ports.port(ModelPolicy.from_spec(spec_model).routes[1])
    assert resolved == []


class _Direct(RecordedTransport):
    kept = False

    def keep_provider_state(self) -> bool:
        self.kept = True
        return True


@pytest.mark.parametrize(("flag", "kept"), [(None, False), ("false", False), ("true", True)])
def test_fl2_fl3_the_direct_transport_keeps_state_only_with_the_flag(
    monkeypatch: pytest.MonkeyPatch, flag: str | None, kept: bool
) -> None:
    import app.agents.agent_loop.direct_transport as direct_module
    import app.agents.agent_loop.factory as factory

    if flag is None:
        monkeypatch.delenv("HARNESS_PROVIDER_STATE", raising=False)
    else:
        monkeypatch.setenv("HARNESS_PROVIDER_STATE", flag)
    monkeypatch.setattr(factory, "_transport_provider", lambda: factory.DIRECT_TRANSPORT)
    # The output cap off, so the arm comes back unwrapped (the cap has its own test_output_cap_transport).
    monkeypatch.setenv("PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS", "0")
    direct = _Direct()
    monkeypatch.setattr(direct_module, "build_direct_transport", lambda *a, **k: direct)
    transport, name = build_transport(None, model_name="gpt-x", model_key="model-1")
    assert (transport, name, direct.kept) == (direct, "direct", kept)
    assert model_module.provider_state_enabled() is kept
