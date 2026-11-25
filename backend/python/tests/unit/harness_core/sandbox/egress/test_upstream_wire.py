"""EG-3 (E0): a sandbox's own upstream crosses the broker's /v1 wire and the proxy's control plane."""

from __future__ import annotations

from harness_core.sandbox.egress.types import EgressBinding
from harness_core.sandbox.egress.wire import BINDING
from harness_core.sandbox.remote.wire import SPEC
from harness_core.sandbox.types import (
    EgressDestination,
    EgressPolicy,
    RuntimeMode,
    SandboxSpec,
    TokenRoute,
)

POLICY = EgressPolicy(
    destinations=(
        EgressDestination("model-gateway", (8097,)),
        EgressDestination("tool-gateway", (8097,), upstream="http://worker-7:8090/harness/tool-gateway/k1"),
    ),
    token_routes=(TokenRoute("ph-tools-0123456789abcdef", "tool-gateway", "gateway:tools"),),
)


def test_eg3_the_upstream_round_trips_on_both_wires() -> None:
    spec = SandboxSpec(image_digest="pxh-opencode:1.18.34", mode=RuntimeMode.DOCKER_HARDENED, egress=POLICY)
    assert SPEC.validate_json(SPEC.dump_json(spec)) == spec
    binding = EgressBinding("sbx-1", "10.213.0.2", "10.213.0.3", POLICY, {"runId": "r1"})
    assert BINDING.validate_json(BINDING.dump_json(binding)) == binding


def test_eg3_a_destination_from_an_older_client_has_no_upstream() -> None:
    spec = SPEC.validate_python(
        {"image_digest": "x", "mode": "docker", "egress": {"destinations": [{"host": "tool-gateway", "ports": [8097]}]}}
    )
    assert spec.egress.destinations[0].upstream is None
