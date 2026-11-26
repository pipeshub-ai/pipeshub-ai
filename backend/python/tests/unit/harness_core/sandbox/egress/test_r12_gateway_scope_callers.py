"""Round 12 (WP-61 x WP-57 x WP-59): what the SDK stubs and the Gemini gateway probe send is gateway traffic.

WP-61 counts only outside traffic toward a run's egress cap and the org baseline; the Model and Tool Gateways count
for metrics only. WP-57's stubs call ``ph``, which reads the Tool Gateway's URL from the session folder
(``gateway.json``, ``endpoints.tools_mcp_url``); WP-59's probe asks the same URL before ``session/new`` (the ACP launch
policy's ``toolsUrl``). Both cross the egress proxy.

R12-EGS-1 the URL both use is the Tool Gateway's alias, so their bytes are gateway bytes: a run whose outside traffic
          is capped still reaches it, unmarked, and nothing it moves counts toward the cap or the baseline.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from harness_core.engines.controller import EngineControllerConfig
from harness_core.sandbox.egress import ProxyRequest
from harness_core.sandbox.egress.policy import EGRESS_CAPPED_HEADER
from harness_core.sandbox.egress.volume import (
    GATEWAY_ALIASES,
    MIB,
    VolumeLimits,
    alias_for,
)

from .test_wp45_egress_volume import A, make_rig

NO_ALERTS = {"alert_up_bytes": 0, "alert_down_bytes": 0, "baseline_factor": 0.0}
ENDPOINTS = EngineControllerConfig(images={}).endpoints("pxh-model-aaaa", "pxh-tools-bbbb")


def _stub_or_probe_call() -> ProxyRequest:
    url = urlsplit(ENDPOINTS.tools_mcp_url)
    return ProxyRequest("POST", ENDPOINTS.tools_mcp_url, (("Host", url.netloc),), b"{}")


async def test_r12_egs_1_the_stubs_and_the_gemini_probe_are_gateway_traffic_even_for_a_capped_run() -> None:
    host = urlsplit(ENDPOINTS.tools_mcp_url).hostname or ""
    assert alias_for(host, "http") == "tool-gateway" and host in GATEWAY_ALIASES
    rig = make_rig(VolumeLimits(max_run_bytes=1000, **NO_ALERTS))
    await rig.proxy.bindings.register(A)
    assert not rig.volume.meter(A, "other").sent(1001), "outside traffic caps the run"
    forward = await rig.proxy.authorize(_stub_or_probe_call(), local=A.proxy_address, peer=A.sandbox_address)
    assert [v for k, v in forward.headers if k.lower() == EGRESS_CAPPED_HEADER.lower()] == []
    assert forward.meter is not None
    assert forward.meter.sent(20 * MIB) and forward.meter.received(20 * MIB) and not forward.meter.stopped
    moved = rig.volume.volume(org_id="o1", run_id="r1")
    assert moved is not None and moved.outside_up + moved.outside_down == 1001
