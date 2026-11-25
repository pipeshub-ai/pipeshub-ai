"""R38IDENT-FU-7: the egress proxy's credential mints a gateway token only for a run attempt the broker put behind it.

The broker signs each binding it registers (``app/harness/sandbox/binding_proof.py``); the proxy forwards the proof
and cannot make one. Node's half and the HTTP bypasses are ``sandbox-binding.test.ts`` and
``gateway-token.http.test.ts``; the live round trip against Node is ``test_node_token_source_contract.py``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.sandbox.binding_proof import (
    BindingAttester,
    binding_message,
    binding_proof,
    broker_binding_key,
    ring_binding_key,
)
from app.harness.sandbox.docker_egress import DockerEgressWiring
from app.utils.encryption.component_key import component_subkey
from harness_core.clock import SystemClock
from harness_core.engines.attempt_labels import bound_labels
from harness_core.sandbox.egress import wire
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.types import EgressBinding
from harness_core.sandbox.types import RuntimeMode, SandboxSpec

REPO = Path(__file__).resolve().parents[6]
VECTORS = json.loads((REPO / "contracts" / "secret-key" / "sandbox-binding.json").read_text(encoding="utf-8"))
KEY = bytes([3]) * 32
LABELS = {"orgId": "org-1", "sessionId": "s-1", "runId": "run-1", "attemptId": "run-1.a1"}
COMPONENT_KEY = base64.b64encode(hashlib.sha256(b"r41ops fu7 broker component key").digest()).decode()


def test_vectors_match_node() -> None:
    broker = component_subkey("sandbox-broker", VECTORS["brokerComponentKey"], "binding")
    assert broker.hex() == VECTORS["brokerBindingKey"]
    ring = ring_binding_key(VECTORS["ringSecret"])
    assert ring.hex() == VECTORS["ringBindingKey"]
    for case in VECTORS["cases"]:
        fields = (case["orgId"], case["runId"], case["attemptId"])
        assert binding_message(*fields).hex() == case["message"]
        assert binding_proof(broker, *fields) == case["brokerProof"]
        assert binding_proof(ring, *fields) == case["ringProof"]


def test_the_proof_is_a_mac_over_length_prefixed_fields() -> None:
    mac = hmac.new(KEY, binding_message("o", "r", "a"), hashlib.sha256).digest()
    assert binding_proof(KEY, "o", "r", "a") == "v1." + base64.urlsafe_b64encode(mac).rstrip(b"=").decode()
    # Moving a boundary between fields changes the message.
    assert binding_message("o", "rg-1run-1", "a") != binding_message("org-1", "run-1", "a")
    assert binding_message("ab", "", "c") != binding_message("a", "b", "c")


@pytest.mark.parametrize(
    "labels",
    [
        LABELS,
        # A kept sign-in sandbox names the attempt it serves now under the bound keys (SIGNFIX).
        {"orgId": "org-1", **bound_labels("run-9", "run-9.a3")},
    ],
)
def test_the_attester_signs_what_the_proxy_reads(labels: dict[str, str]) -> None:
    binding = BindingAttester(KEY).attest(EgressBinding("sbx-1", "10.0.0.2", "10.0.0.3", labels=labels))
    run_id = labels.get("runId") or labels["pipeshub.bound.runId"]
    attempt_id = labels.get("attemptId") or labels["pipeshub.bound.attemptId"]
    assert binding.attestation == binding_proof(KEY, "org-1", run_id, attempt_id)


@pytest.mark.parametrize(
    "labels",
    [{}, {"runId": "run-1", "attemptId": "a"}, {"orgId": "org-1", "runId": "run-1"}, {**LABELS, "orgId": ""}],
)
def test_a_binding_naming_no_whole_attempt_carries_no_proof(labels: dict[str, str]) -> None:
    binding = BindingAttester(KEY).attest(
        EgressBinding("sbx-1", "10.0.0.2", "10.0.0.3", labels=labels, attestation="v1.stale")
    )
    assert binding.attestation is None


def test_a_broker_without_a_key_signs_nothing_and_drops_a_stale_proof() -> None:
    binding = EgressBinding("sbx-1", "10.0.0.2", "10.0.0.3", labels=LABELS, attestation="v1.stale")
    assert BindingAttester(None).attest(binding).attestation is None


def test_the_proof_stays_out_of_reprs_and_survives_the_wire_and_the_proxys_sealed_state() -> None:
    binding = BindingAttester(KEY).attest(EgressBinding("sbx-1", "10.0.0.2", "10.0.0.3", labels=LABELS))
    assert binding.attestation is not None
    assert binding.attestation not in repr(binding)
    dumped = wire.BINDING.dump_python(binding, mode="json")
    assert wire.BINDING.validate_python(dumped) == binding
    # A binding from before the field (a proxy's sealed state across an upgrade) reads as unattested.
    old = {k: v for k, v in dumped.items() if k != "attestation"}
    assert wire.BINDING.validate_python(old).attestation is None


def test_broker_key_from_its_component_key_and_never_on_the_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("SECRET_KEY", "SECRET_KEY_PREVIOUS", "SECRET_KEY_NEXT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PIPESHUB_COMPONENT", "sandbox-broker")
    monkeypatch.setenv("PIPESHUB_COMPONENT_KEY", COMPONENT_KEY)
    assert broker_binding_key() == component_subkey("sandbox-broker", COMPONENT_KEY, "binding")
    monkeypatch.setenv("PIPESHUB_COMPONENT", "egress-proxy")
    assert broker_binding_key() is None


def test_broker_key_from_the_ring_when_it_holds_secret_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PIPESHUB_COMPONENT", raising=False)
    monkeypatch.delenv("SECRET_KEY_PREVIOUS", raising=False)
    monkeypatch.delenv("SECRET_KEY_NEXT", raising=False)
    secret = base64.b64encode(hashlib.sha256(b"r41ops fu7 ring test").digest()).decode()
    monkeypatch.setenv("SECRET_KEY", secret)
    assert broker_binding_key() == ring_binding_key(secret)
    monkeypatch.delenv("SECRET_KEY")
    assert broker_binding_key() is None


class _Recorder:
    def __init__(self) -> None:
        self.registered: list[EgressBinding] = []
        self.instance = "proxy-1"

    async def register(self, binding: EgressBinding) -> None:
        self.registered.append(binding)

    async def health(self) -> Any:  # noqa: ANN401 - only .instance is read
        return type("Health", (), {"instance": self.instance})()

    async def bound(self) -> frozenset[str]:
        return frozenset(b.sandbox_id for b in self.registered)

    async def unregister(self, sandbox_id: str) -> None:
        return None


def _wiring(recorder: _Recorder, key: bytes) -> DockerEgressWiring:
    wiring = DockerEgressWiring(
        provider=None,  # pyright: ignore[reportArgumentType] - bind and resync reach no daemon here
        clock=SystemClock(),
        proxy_container="egress-proxy",
        bindings=recorder,  # pyright: ignore[reportArgumentType]
        pool=SubnetPool("10.214.0.0/16"),
        network_prefix="pxh-",
        attester=BindingAttester(key),
    )
    wiring._subnets["sbx-1"] = "10.214.0.8/29"  # pyright: ignore[reportPrivateUsage]
    return wiring


async def test_the_broker_signs_every_binding_it_registers_and_signs_again_on_resync() -> None:
    recorder = _Recorder()
    wiring = _wiring(recorder, KEY)
    spec = SandboxSpec(image_digest="sha256:" + "0" * 64, mode=RuntimeMode.GVISOR, labels=LABELS)
    _ = await wiring.bind("sbx-1", spec)
    (first,) = recorder.registered
    assert first.attestation == binding_proof(KEY, "org-1", "run-1", "run-1.a1")
    # A key changed under the broker (a SECRET_KEY rotation): the restarted proxy gets the binding signed anew.
    wiring._attester = BindingAttester(bytes([4]) * 32)  # pyright: ignore[reportPrivateUsage]
    assert await wiring.resync()
    assert recorder.registered[-1].attestation == binding_proof(bytes([4]) * 32, "org-1", "run-1", "run-1.a1")


async def test_a_rebound_kept_sandbox_carries_the_new_attempts_proof_only() -> None:
    recorder = _Recorder()
    wiring = _wiring(recorder, KEY)
    image = "sha256:" + "0" * 64
    kept = {"orgId": "org-1", "pipeshub.kept": "1"}
    _ = await wiring.bind("sbx-1", SandboxSpec(image, RuntimeMode.GVISOR, labels={**kept, **bound_labels("run-1", "a1")}))
    _ = await wiring.bind("sbx-1", SandboxSpec(image, RuntimeMode.GVISOR, labels={**kept, **bound_labels("run-2", "a1")}))
    first, second = recorder.registered
    assert first.attestation != second.attestation
    assert second.attestation == binding_proof(KEY, "org-1", "run-2", "a1")
