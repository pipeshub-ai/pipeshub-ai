"""R13-WEB-1..2: an allowed website's traffic is outside traffic (WP-63 sites x WP-45 egress volume x WP-56's web
allowance).

A run's web allowance (WP-56) counts the PipesHub web tools' searches and page reads, which leave through the web
readers gateway and SearXNG, never through a sandbox. An engine reaching an allowed website from its sandbox is the
egress proxy's: those bytes are the run's outside bytes, under the deployment's per-run cap and its alerts, as a
tunnel's are, and never a gateway's (which a capped run still reaches).
"""

from __future__ import annotations

import pytest

from harness_core.sandbox.egress import EgressRefusalReason, EgressRefused, ProxyRequest
from harness_core.sandbox.egress.volume import EgressVolume, VolumeLimits
from tests.unit.harness_core.sandbox.egress import test_wp63_allowed_sites as sites


async def _proxy_with(limits: VolumeLimits):  # noqa: ANN202 - EgressProxy
    proxy, _, _ = await sites._proxy()  # pyright: ignore[reportPrivateUsage]
    proxy.volume = EgressVolume(limits=limits, clock=proxy.volume._clock, bindings=proxy.bindings)  # pyright: ignore[reportPrivateUsage]
    return proxy


def _read() -> ProxyRequest:
    return sites._inside("/api/v1")  # pyright: ignore[reportPrivateUsage]


async def test_r13_web_1_a_websites_bytes_are_the_runs_outside_bytes() -> None:
    proxy = await _proxy_with(VolumeLimits())
    forward = await sites._intercepted(proxy, _read())  # pyright: ignore[reportPrivateUsage]
    assert forward.meter is not None
    assert forward.meter.received(4096) is True
    moved = proxy.volume.volume(org_id="o1", run_id="r1")
    assert moved is not None and moved.outside_down == 4096


async def test_r13_web_2_a_run_past_its_cap_reaches_no_website() -> None:
    proxy = await _proxy_with(VolumeLimits(max_run_bytes=1024))
    forward = await sites._intercepted(proxy, _read())  # pyright: ignore[reportPrivateUsage]
    assert forward.meter is not None
    assert forward.meter.received(2048) is False, "the stream that crossed the cap stops"
    with pytest.raises(EgressRefused) as refused:
        _ = await sites._intercepted(proxy, _read())  # pyright: ignore[reportPrivateUsage]
    assert refused.value.reason is EgressRefusalReason.VOLUME_CAP_EXCEEDED
