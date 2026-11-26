"""CPD-6 MAC-G2 (wiring): which narrowing the model gateway reads, by ``CONTROL_PLANE_PIN``.

``on``: the org's live layer from Node's live route (no model-access document read per call); anything else: the
document, as before. A value the switch does not know stops the role, as it stops Node.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app import model_gateway_main as gateway
from app.config.constants.service import config_node_constants
from app.harness.access.live_policies import LiveOrgAccessPolicies
from app.harness.unpinned import ConfiguredOrgAccessPolicies
from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.testing import ManualClock
from tests.support.config_service import FakeConfigurationService

ORG = "org-1"
MODEL_ACCESS = config_node_constants.AGENT_HARNESS_MODEL_ACCESS.value
WORKER_CREDENTIAL = config_node_constants.AGENT_HARNESS_WORKER_CREDENTIAL.value


def _config() -> FakeConfigurationService:
    return FakeConfigurationService(
        {
            MODEL_ACCESS: {"orgs": {ORG: {"disabledRules": ["from-the-document"], "consumerPlans": False}}},
            WORKER_CREDENTIAL: {"current": "worker-credential-not-real"},
        }
    )


@pytest.mark.parametrize("value", ["off", "shadow", " Shadow "])
async def test_mac_g2_without_the_pin_the_gateway_reads_the_document_per_call(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv(gateway.ENV_CONTROL_PLANE_PIN, raising=False)
    else:
        monkeypatch.setenv(gateway.ENV_CONTROL_PLANE_PIN, value)
    config = _config()

    policies = gateway.org_access_policies(config, node_url="http://node.internal:3002", clock=ManualClock())  # type: ignore[arg-type]

    assert isinstance(policies, ConfiguredOrgAccessPolicies)
    assert (await policies.policy(ORG)).disabled_rules == frozenset({"from-the-document"})


@pytest.mark.parametrize("value", [None, "", "ON"])
async def test_mac_g2_with_the_pin_on_the_gateway_reads_the_live_layer_and_no_document(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    """C4.04: unset or blank is on."""
    if value is None:
        monkeypatch.delenv(gateway.ENV_CONTROL_PLANE_PIN, raising=False)
    else:
        monkeypatch.setenv(gateway.ENV_CONTROL_PLANE_PIN, value)
    config = _config()
    asked: list[str] = []

    def node(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        layer = {"schemaVersion": 1, "scope": {"kind": "live"}, "models": {"disabledRules": ["from-the-live-layer"]}}
        body = {"schemaVersion": 1, "orgId": ORG, "revision": 7, "layers": [{"layer": layer}], "revokedBindings": []}
        return httpx.Response(200, content=json.dumps(body))

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _with_transport(httpx.MockTransport(node)))

    policies = gateway.org_access_policies(config, node_url="http://node.internal:3002", clock=ManualClock())  # type: ignore[arg-type]

    assert isinstance(policies, LiveOrgAccessPolicies)
    assert (await policies.policy(ORG)).disabled_rules == frozenset({"from-the-live-layer"})
    assert (await policies.policy(ORG)).disabled_rules == frozenset({"from-the-live-layer"})
    assert asked == [f"/api/v1/agent-harness/internal/control-plane/orgs/{ORG}/live"], "in process after the first read"
    assert MODEL_ACCESS not in config.reads


async def test_mac_g2_with_the_pin_on_an_unreachable_live_route_refuses_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gateway.ENV_CONTROL_PLANE_PIN, "on")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("node is down")

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _with_transport(httpx.MockTransport(down)))
    policies = gateway.org_access_policies(_config(), node_url="http://node.internal:3002", clock=ManualClock())  # type: ignore[arg-type]

    with pytest.raises(AccessPolicyUnavailable):
        _ = await policies.policy(ORG)


def test_mac_g2_a_value_the_switch_does_not_know_stops_the_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(gateway.ENV_CONTROL_PLANE_PIN, "enabled")

    with pytest.raises(gateway.InvalidControlPlanePin):
        _ = gateway.org_access_policies(_config(), node_url="http://node.internal:3002", clock=ManualClock())  # type: ignore[arg-type]


def _with_transport(transport: httpx.MockTransport):  # noqa: ANN202
    """Node as the gateway's HTTP client reaches it: the outermost I/O, faked at the transport."""
    original = httpx.AsyncClient.__init__

    def init(self: httpx.AsyncClient, *args: object, **kwargs: object) -> None:
        kwargs["transport"] = transport
        original(self, *args, **kwargs)  # type: ignore[arg-type]

    return init
