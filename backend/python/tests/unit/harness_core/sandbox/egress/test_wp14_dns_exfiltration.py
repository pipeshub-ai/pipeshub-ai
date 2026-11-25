"""SEC-EG-02 (C6.07): a name that carries encoded data is refused like any other, alerted on, and kept out of logs.

The sandbox has no resolver (the Docker lane proves it); the proxy is the only place a name goes, and it refuses every
name not on the allowlist before any lookup. A refused name whose labels read as encoded data is logged as
``egress.exfiltration_suspected``, and neither that alert, the refusal log nor the run's refusal report carries the
encoded label.
"""

from __future__ import annotations

import logging

import pytest

from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyRequest,
    StaticResolver,
)
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.policy import exfiltration_suspect, redacted_host
from harness_core.sandbox.types import EgressDestination, EgressPolicy
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

SECRET_LABEL = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


@pytest.mark.parametrize(
    "host",
    [
        f"{SECRET_LABEL}.exfil.example",
        "dxnlcjpwyxnzd29yza.evil.example",
        "mzxw6ytboizxw6ytboi2a.example",
        ".".join(["abcdefghij" * 3] * 4) + ".evil.example",
    ],
)
def test_sec_eg_02_encoded_labels_are_suspects_and_redacted(host: str) -> None:
    assert exfiltration_suspect(host)
    shown = redacted_host(host)
    assert shown.endswith(".example") and "…" in shown
    assert SECRET_LABEL not in shown and "dxnlcjpwyxnzd29yza" not in shown


@pytest.mark.parametrize(
    "host",
    [
        "example.com",
        "files.pythonhosted.org",
        "d36jcksde1wxzq.cloudfront.net",
        "my-company-internal-artifactory.example.com",
        "kubernetes-dashboard-production-cluster.example.com",
        "ec2-54-12-34-56.compute-1.amazonaws.com",
    ],
)
def test_sec_eg_02_ordinary_names_are_not_suspects(host: str) -> None:
    assert not exfiltration_suspect(host)
    assert redacted_host(host) == host


async def test_sec_eg_02_a_suspect_name_is_refused_alerted_and_never_logged(caplog: pytest.LogCaptureFixture) -> None:
    clock = ManualClock()
    bindings = InMemoryEgressBindings()
    proxy = EgressProxy(
        bindings=bindings,
        tokens=InMemoryTokenSource(b"s", clock),
        resolver=StaticResolver(),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
    )
    binding = EgressBinding(
        "sbx-x", "10.213.0.2", "10.213.0.3", EgressPolicy(destinations=(EgressDestination("docs.example.org", (80,)),)),
        {"runId": "r1"},
    )
    await bindings.register(binding)
    caplog.set_level(logging.INFO)
    for host, suspect in ((f"{SECRET_LABEL}.exfil.example", True), ("plain.example", False)):
        caplog.clear()
        request = ProxyRequest("GET", f"http://{host}/", (), b"")
        with pytest.raises(EgressRefused) as caught:
            _ = await proxy.authorize(request, local=binding.proxy_address, peer=binding.sandbox_address)
        assert caught.value.reason is R.DESTINATION_NOT_ALLOWED
        assert ("egress.exfiltration_suspected" in caplog.text) is suspect
        assert "egress.refused" in caplog.text
        assert SECRET_LABEL not in caplog.text
