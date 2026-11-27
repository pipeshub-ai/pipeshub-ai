"""R36-COVER: a sandbox with no egress keeps its loopback on gVisor.

Found on the journey runner's hub lane at the production floor (runsc): every STDIO MCP runner failed to start,
``runner_start_failed``, because its bridge could not bind 127.0.0.1 (``Cannot assign requested address``). The broker
created a no-egress sandbox with ``NetworkDisabled``; on runc that still leaves loopback, on gVisor it leaves the
sandbox's netstack with no interface at all. ``NetworkMode=none`` alone keeps gVisor's loopback and no route out.
"""

from __future__ import annotations

import pytest

from app.harness.sandbox.docker_broker import DockerCapabilities
from harness_core.sandbox.types import EgressDefault, EgressPolicy, RuntimeMode
from tests.unit.harness.sandbox_broker.test_open_egress_firewall import (
    LEASE,
    _Api,
    _broker,
    _Provider,
    _spec,
)

CAPS = DockerCapabilities(True, "28.0.4", frozenset({"runc", "runsc"}), "unix")
NONE = EgressPolicy(default=EgressDefault.DENY)


@pytest.mark.parametrize(("mode", "disabled"), [(RuntimeMode.GVISOR, False), (RuntimeMode.DOCKER_HARDENED, True)])
async def test_a_sandbox_with_no_egress_has_no_route_out_and_on_gvisor_keeps_loopback(
    mode: RuntimeMode, disabled: bool,
) -> None:
    api = _Api()
    await _broker(_Provider(api), CAPS).acquire(_spec(mode=mode, egress=NONE), lease=LEASE, idempotency_key="k")
    [sandbox] = api.created
    assert sandbox["host_config"]["NetworkMode"] == "none"
    assert sandbox["network_disabled"] is disabled


