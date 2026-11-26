"""QA31F: a sandbox's egress network is made while other networks on the daemon hold the pool's first subnets.

The QA31 rows PW-013, PW-027 and PW-042 failed with "the egress subnet pool is exhausted" on a /16 pool, because
58 networks other lanes had left on the shared daemon held its lowest /29s and the broker tried one subnet per
attempt. No daemon: a fake API that answers Docker's overlap refusal.
"""

from __future__ import annotations

import asyncio
import ipaddress
from dataclasses import dataclass, field
from typing import Any

from app.harness.sandbox.docker_egress import DockerEgressWiring, EgressLabels
from harness_core.sandbox.egress.addresses import SubnetPool

POOL = "10.214.0.0/16"
OVERLAP = "Pool overlaps with other one on this address space"


class APIError(Exception):
    pass


def _overlaps(cidr: str, held: set[str]) -> bool:
    net = ipaddress.ip_network(cidr)
    return any(net.overlaps(ipaddress.ip_network(h)) for h in held)


@dataclass
class FakeApi:
    held: set[str] = field(default_factory=set[str])
    creates: int = 0

    def create_network(self, name: str, **kwargs: Any) -> dict[str, str]:  # noqa: ANN401
        self.creates += 1
        subnet = kwargs["ipam"]["Config"][0]["Subnet"]
        if _overlaps(subnet, self.held):
            raise APIError(f'400 Client Error: Bad Request ("invalid pool request: {OVERLAP}")')
        self.held.add(subnet)
        return {"Id": name}

    def networks(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if filters:
            return []
        return [{"Name": f"other-{i}", "IPAM": {"Config": [{"Subnet": s}]}} for i, s in enumerate(sorted(self.held))]

    def connect_container_to_network(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None

    def disconnect_container_from_network(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None

    def remove_network(self, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        return None


@dataclass
class FakeClient:
    api: FakeApi


class FakeProvider:
    def __init__(self, api: FakeApi) -> None:
        self.client = FakeClient(api)

    async def run_blocking(self, fn: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        return fn(*args, **kwargs)


class FakeBindings:
    async def unregister(self, sandbox_id: str) -> None:
        return None


def _wiring(api: FakeApi) -> DockerEgressWiring:
    wiring = DockerEgressWiring(
        provider=FakeProvider(api),  # pyright: ignore[reportArgumentType]
        clock=None,  # pyright: ignore[reportArgumentType]
        proxy_container="pxh-proxy",
        bindings=FakeBindings(),  # pyright: ignore[reportArgumentType]
        pool=SubnetPool(POOL),
        network_prefix="pxh-t-net-",
    )
    wiring.bind_labels(EgressLabels(managed="m", sandbox_id="s", created="c", role="r"))
    return wiring


def test_qa31f_1_networks_left_on_the_daemon_do_not_exhaust_a_large_pool() -> None:
    """70 foreign /29s at the pool's start (more than the broker's attempts): the next free one is used."""
    lowest = list(ipaddress.ip_network(POOL).subnets(new_prefix=29))
    api = FakeApi(held={str(n) for n in lowest[:70]})
    subnet = asyncio.run(_wiring(api).prepare("sbx_1", {}))
    assert subnet.cidr == str(lowest[70])
    assert api.creates <= 2


def test_qa31f_2_a_foreign_network_larger_than_a_slot_is_skipped_whole() -> None:
    """A /24 of another network inside the pool covers 32 slots; none of them is tried."""
    api = FakeApi(held={"10.214.0.0/24"})
    subnet = asyncio.run(_wiring(api).prepare("sbx_1", {}))
    assert subnet.cidr == "10.214.1.0/29"
    assert api.creates <= 2


def test_qa31f_3_a_full_pool_is_still_reported() -> None:
    api = FakeApi(held={POOL})
    wiring = _wiring(api)
    try:
        asyncio.run(wiring.prepare("sbx_1", {}))
    except Exception as exc:  # the broker's own error type is what is asserted
        assert "exhausted" in str(exc)
    else:
        raise AssertionError("a pool every subnet of which is held must be refused")
